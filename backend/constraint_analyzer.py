from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from typing import Optional

import requests

logger = logging.getLogger(__name__)

_OPENAI_CHAT_URL = "https://api.openai.com/v1/chat/completions"

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
    by_match = re.search(r"by\s+(\d{1,2})(?:\s*:\s*(\d{2}))?\s*(am|pm)?", text)
    if by_match:
        hour = int(by_match.group(1))
        am_pm = by_match.group(3)
        if am_pm == "pm" and hour < 12:
            hour += 12
        if am_pm == "am" and hour == 12:
            hour = 0
        hour = max(0, min(24, hour))
        constraints = {
            appliance_id: {
                "maxShiftHours": None,
                "allowedWindows": [{"startHour": 0, "endHour": hour}],
            }
            for appliance_id in appliance_ids
        }
        return AnalyzeConstraintResult(appliance_constraints=constraints)

    constraints = {
        appliance_id: {"maxShiftHours": None, "allowedWindows": None}
        for appliance_id in appliance_ids
    }
    return AnalyzeConstraintResult(appliance_constraints=constraints)


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

        normalized_constraints[appliance_id] = {
            "maxShiftHours": max_shift_hours,
            "allowedWindows": normalized_windows,
        }

    return AnalyzeConstraintResult(appliance_constraints=normalized_constraints)
