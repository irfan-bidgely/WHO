from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from typing import Optional

import requests


def _clock_hour_to_24(hour: int, am_pm: Optional[str]) -> int:
    h = int(hour)
    ap = (am_pm or "").lower()
    if ap == "pm" and h < 12:
        h += 12
    if ap == "am" and h == 12:
        h = 0
    return max(0, min(24, h))

logger = logging.getLogger(__name__)

_OPENAI_CHAT_URL = "https://api.openai.com/v1/chat/completions"


def half_open_span_hours_from_windows(windows: object) -> int:
    """
    Longest contiguous span (hours) implied by each window in half-open [start, end).
    endHour 24 counts as end-of-day (24) so e.g. 22–24 => span 2.
    For multiple windows, returns the maximum single-window span (a block must fit
    entirely in one window).
    """
    if not isinstance(windows, list) or not windows:
        return 0
    best = 0
    for w in windows:
        if not isinstance(w, dict):
            continue
        try:
            s = int(w["startHour"])
            e = int(w["endHour"])
        except (KeyError, TypeError, ValueError):
            continue
        e_eff = 24 if e >= 24 else e
        best = max(best, max(1, e_eff - s))
    return best

CONSTRAINT_ANALYZER_PROMPT_TEMPLATE = """
You extract scheduling constraints per appliance from user natural language.

Return JSON only with this exact schema:
{{
  "applianceConstraints": [
    {{
      "applianceId": int,
      "maxShiftHours": number | null,
      "allowedWindows": [{{"startHour": int, "endHour": int}}] | null
    }}
  ]
}}

Rules:
- Appliance ids must be one of shiftable appliance ids: {shiftable_ids}.
- Appliance name/id catalog: {appliance_catalog}.
- Include only appliances explicitly referenced by user text.
- Hours must be integers in [0, 24].
- Use 24h clock.
- If user says "by 7AM", interpret as allowed window ending at 7 => startHour 0, endHour 7.
- If user says "after 10PM", interpret as 22 to 24.
- If user says "between 6AM and 9AM", interpret as 6 to 9.
- If nothing is specified for a block field, return null for it.
- Never add extra keys.
""".strip()


@dataclass
class AnalyzeConstraintResult:
    appliance_constraints: dict[int, dict[str, object]]


def _extract_json_object(raw: str) -> dict:
    text = raw.strip()
    match = re.search(r"\{.*\}", text, flags=re.DOTALL)
    candidate = match.group(0) if match else text
    parsed = json.loads(candidate)
    if not isinstance(parsed, dict):
        raise ValueError("Model output must be a JSON object")
    return parsed


def _infer_appliances(constraint_text: str) -> list[int]:
    text = constraint_text.lower()
    found: list[int] = []
    if re.search(r"\bev\b|\belectric vehicle\b", text):
        found.append(18)
    if re.search(r"\bpool\b|\bpool pump\b", text):
        found.append(2)
    if re.search(r"\bspace heater\b", text):
        found.append(3)
    if re.search(r"\bac\b|\bair\s*conditioner\b|\bcooling\b", text):
        found.append(4)
    if re.search(r"\bwater heater\b|\bgeyser\b|\bhot water\b", text):
        found.append(7)
    return found


def _fallback_parse(constraint_text: str) -> AnalyzeConstraintResult:
    text = constraint_text.lower()
    appliance_ids = _infer_appliances(text) or [18]

    def _for_all(windows: list[dict[str, int]]) -> AnalyzeConstraintResult:
        span = half_open_span_hours_from_windows(windows)
        return AnalyzeConstraintResult(
            appliance_constraints={
                aid: {
                    "maxShiftHours": None,
                    "allowedWindows": windows,
                    "_halfOpenSpanHours": span,
                }
                for aid in appliance_ids
            }
        )

    # "between 6am and 9am" / "between 6 and 9"
    between_m = re.search(
        r"between\s+(\d{1,2})(?:\s*([ap]m))?\s+and\s+(\d{1,2})(?:\s*([ap]m))?",
        text,
    )
    if between_m:
        h1 = _clock_hour_to_24(int(between_m.group(1)), between_m.group(2))
        h2 = _clock_hour_to_24(int(between_m.group(3)), between_m.group(4))
        lo, hi = (h1, h2) if h1 <= h2 else (h2, h1)
        return _for_all([{"startHour": lo, "endHour": hi}])

    # "before 2 am" / "before 2:30 am" (minutes ignored; end hour uses clock hour)
    before_m = re.search(
        r"before\s+(\d{1,2})(?:\s*:\s*\d{2})?\s*([ap]m)?",
        text,
    )
    if before_m:
        end_h = _clock_hour_to_24(int(before_m.group(1)), before_m.group(2))
        return _for_all([{"startHour": 0, "endHour": end_h}])

    # "after 10 pm" / "after 10"
    after_m = re.search(
        r"after\s+(\d{1,2})(?:\s*:\s*\d{2})?\s*([ap]m)?",
        text,
    )
    if after_m:
        start_h = _clock_hour_to_24(int(after_m.group(1)), after_m.group(2))
        return _for_all([{"startHour": start_h, "endHour": 24}])

    by_match = re.search(r"by\s+(\d{1,2})(?:\s*:\s*(\d{2}))?\s*(am|pm)?", text)
    if by_match:
        hour = int(by_match.group(1))
        am_pm = by_match.group(3)
        hour = _clock_hour_to_24(hour, am_pm)
        return _for_all([{"startHour": 0, "endHour": hour}])

    constraints = {
        appliance_id: {"maxShiftHours": None, "allowedWindows": None}
        for appliance_id in appliance_ids
    }
    return AnalyzeConstraintResult(appliance_constraints=constraints)


def merge_fallback_where_windows_missing(
    constraint_text: str,
    constraints: dict[int, dict[str, object]],
    *,
    shiftable_appliance_ids: list[int],
) -> dict[int, dict[str, object]]:
    """
    If LLM (or prior step) left allowedWindows/maxShiftHours empty for an appliance,
    fill from rule-based fallback so phrases like "before 2 AM" still work.
    """
    fb = _fallback_parse(constraint_text).appliance_constraints
    out = dict(constraints)
    for aid in list(out.keys()):
        if aid not in shiftable_appliance_ids:
            continue
        c = out[aid]
        win = c.get("allowedWindows")
        msh = c.get("maxShiftHours")
        has_window = win is not None and isinstance(win, list) and len(win) > 0
        has_shift = msh is not None
        if has_window or has_shift:
            continue
        if aid in fb:
            out[aid] = dict(fb[aid])
    # Also add appliances that fallback inferred but LLM omitted entirely
    for aid, fc in fb.items():
        if aid not in out and aid in shiftable_appliance_ids:
            fw = fc.get("allowedWindows")
            if fw is not None and isinstance(fw, list) and len(fw) > 0:
                out[aid] = dict(fc)
    return out


def filter_constraints_to_inferred_appliances(
    constraint_text: str,
    constraints: dict[int, dict[str, object]],
    *,
    shiftable_appliance_ids: list[int],
) -> dict[int, dict[str, object]]:
    """
    For natural-language input, drop appliances the model invented that are not
    referenced in the text (e.g. AC when user only said "Charge EV before 2 AM").
    If inference finds nothing, leave ``constraints`` unchanged (trust LLM / payload).
    """
    text = (constraint_text or "").strip().lower()
    if not text:
        return constraints
    inferred = set(_infer_appliances(text))
    if not inferred:
        return constraints
    filtered = {
        int(k): v
        for k, v in constraints.items()
        if int(k) in inferred and int(k) in shiftable_appliance_ids
    }
    if filtered:
        return filtered
    fb = _fallback_parse(constraint_text).appliance_constraints
    return {
        k: v
        for k, v in fb.items()
        if k in shiftable_appliance_ids and k in inferred
    }


def analyze_constraint_text(
    constraint_text: str,
    *,
    shiftable_appliance_ids: list[int],
    appliance_catalog: dict[int, str],
) -> AnalyzeConstraintResult:
    api_key = (os.getenv("OPENAI_API_KEY") or "").strip()
    if not api_key:
        logger.warning("OPENAI_API_KEY missing; using fallback parser for constraints")
        fallback = _fallback_parse(constraint_text)
        filtered = {
            k: v
            for k, v in fallback.appliance_constraints.items()
            if k in shiftable_appliance_ids
        }
        return AnalyzeConstraintResult(appliance_constraints=filtered)

    shiftable_catalog = {
        appliance_id: appliance_catalog.get(appliance_id, f"APPLIANCE_{appliance_id}")
        for appliance_id in shiftable_appliance_ids
    }
    prompt = CONSTRAINT_ANALYZER_PROMPT_TEMPLATE.format(
        shiftable_ids=shiftable_appliance_ids,
        appliance_catalog=shiftable_catalog,
    )

    payload = {
        "model": "gpt-4o-mini",
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": prompt},
            {
                "role": "user",
                "content": f"Constraint text: {constraint_text}",
            },
        ],
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    response = requests.post(_OPENAI_CHAT_URL, headers=headers, json=payload, timeout=30)
    response.raise_for_status()
    data = response.json()
    content = data["choices"][0]["message"]["content"]
    parsed = _extract_json_object(content)

    appliance_constraints = parsed.get("applianceConstraints")
    if not isinstance(appliance_constraints, list):
        raise ValueError("applianceConstraints must be a list")

    normalized_constraints: dict[int, dict[str, object]] = {}
    for block in appliance_constraints:
        if not isinstance(block, dict):
            continue
        appliance_id_raw = block.get("applianceId")
        if appliance_id_raw is None:
            continue
        appliance_id = int(appliance_id_raw)
        if appliance_id not in shiftable_appliance_ids:
            continue
        max_shift_hours = block.get("maxShiftHours")
        allowed_windows = block.get("allowedWindows")
        if max_shift_hours is not None:
            max_shift_hours = int(max_shift_hours)
        normalized_windows: Optional[list[dict[str, int]]] = None
        if allowed_windows is not None:
            normalized_windows = []
            for window in allowed_windows:
                start_hour = int(window["startHour"])
                end_hour = int(window["endHour"])
                normalized_windows.append({"startHour": start_hour, "endHour": end_hour})

        entry: dict[str, object] = {
            "maxShiftHours": max_shift_hours,
            "allowedWindows": normalized_windows,
        }
        if normalized_windows:
            entry["_halfOpenSpanHours"] = half_open_span_hours_from_windows(
                normalized_windows
            )
        normalized_constraints[appliance_id] = entry

    if not normalized_constraints:
        fb = _fallback_parse(constraint_text)
        normalized_constraints = {
            k: v
            for k, v in fb.appliance_constraints.items()
            if k in shiftable_appliance_ids
        }

    merged = merge_fallback_where_windows_missing(
        constraint_text,
        normalized_constraints,
        shiftable_appliance_ids=shiftable_appliance_ids,
    )
    return AnalyzeConstraintResult(appliance_constraints=merged)
