"""
Build grounded facts from load-shift API payloads and generate user-facing insights via an LLM.

Supports Gemini (GEMINI_API_KEY) or OpenAI (OPENAI_API_KEY); see INSIGHT_LLM_PROVIDER.
Falls back to deterministic text if no key is set or the provider call fails.

One month-level insight per appliance when ``monthly_total_savings`` > 0; otherwise ``insight`` is empty.
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)

_INSIGHTS_DIR = Path(__file__).resolve().parent
_BACKEND_ROOT = _INSIGHTS_DIR.parent
_DEFAULT_MAPPING_PATH = _BACKEND_ROOT / "appliance_mapping.json"

try:
    from dotenv import load_dotenv

    load_dotenv(_BACKEND_ROOT / ".env")
except ImportError:
    pass


class LoadShiftInsightError(ValueError):
    """Invalid load-shift payload."""


def _load_appliance_mapping(path: Path | None = None) -> dict[str, str]:
    p = path or _DEFAULT_MAPPING_PATH
    with open(p, encoding="utf-8") as f:
        raw: dict[str, Any] = json.load(f)
    return {str(k): str(v) for k, v in raw.items()}


def slot_to_user_day_and_hour(slot: int) -> tuple[int, int]:
    """User-facing day (1-based) and clock hour 0–23 from 0-indexed hour slot."""
    if slot < 0:
        raise ValueError("slot must be non-negative")
    day = slot // 24 + 1
    hour = slot % 24
    return day, hour


def format_time_window(start_slot: int, duration: int) -> str:
    """e.g. Day 2 at 04:00 for 1 hour."""
    if duration < 1:
        raise ValueError("duration must be at least 1")
    day, hour = slot_to_user_day_and_hour(start_slot)
    h_label = "hour" if duration == 1 else "hours"
    return f"Day {day} at {hour:02d}:00 for {duration} {h_label}"


def _appliance_name(mapping: dict[str, str], app_id: int) -> str:
    return mapping.get(str(app_id), f"APP_{app_id}")


def _block_fingerprint(block: dict[str, Any]) -> tuple[Any, ...]:
    return (
        block.get("originalStart_t"),
        block.get("newStart_t"),
        block.get("duration"),
    )


def _pattern_notes_from_shift_patterns(shift_patterns: list[dict[str, Any]]) -> dict[str, Any]:
    if not shift_patterns:
        return {
            "shift_event_count": 0,
            "original_windows": [],
            "recommended_windows": [],
            "largest_portion_savings": 0.0,
        }
    origs = [x["original_window"] for x in shift_patterns]
    recs = [x["recommended_window"] for x in shift_patterns]
    largest = max(float(x["portion_savings"]) for x in shift_patterns)
    return {
        "shift_event_count": len(shift_patterns),
        "original_windows": sorted(set(origs)),
        "recommended_windows": sorted(set(recs)),
        "largest_portion_savings": largest,
    }


def build_insight_facts(
    payload: dict[str, Any],
    *,
    mapping: dict[str, str] | None = None,
) -> dict[str, Any]:
    """
    Normalize API payload into monthly, per-appliance facts for one insight per appliance.

    Empty ``{}`` entries in ``blockShifts`` are skipped. All valid blocks are deduped by
    (originalStart_t, newStart_t, duration), then included in ``shift_patterns`` for pattern analysis.
    """
    mapping = mapping or _load_appliance_mapping()
    if not isinstance(payload, dict):
        raise LoadShiftInsightError("payload must be a JSON object")

    meta = payload.get("metadata")
    if not isinstance(meta, dict):
        raise LoadShiftInsightError("metadata must be an object")

    currency = meta.get("currency")
    if not currency or not isinstance(currency, str):
        raise LoadShiftInsightError("metadata.currency must be a non-empty string")

    total_slots = meta.get("totalTimeSlots")
    if not isinstance(total_slots, int) or total_slots < 1:
        raise LoadShiftInsightError("metadata.totalTimeSlots must be a positive integer")

    load_shift = payload.get("loadShift")
    if not isinstance(load_shift, list) or not load_shift:
        raise LoadShiftInsightError("loadShift must be a non-empty array")

    appliances_out: list[dict[str, Any]] = []

    for item in load_shift:
        if not isinstance(item, dict):
            raise LoadShiftInsightError("each loadShift item must be an object")
        app_id = item.get("appId")
        if not isinstance(app_id, int):
            raise LoadShiftInsightError("appId must be an integer")

        total_savings = item.get("totalSavings")
        if not isinstance(total_savings, (int, float)):
            raise LoadShiftInsightError("totalSavings must be a number")

        blocks = item.get("blockShifts")
        if not isinstance(blocks, list) or not blocks:
            raise LoadShiftInsightError("blockShifts must be a non-empty array")

        normalized_blocks: list[dict[str, Any]] = []
        for b in blocks:
            if not isinstance(b, dict):
                raise LoadShiftInsightError("each blockShifts item must be an object")
            if not b:
                continue
            for key in (
                "originalStart_t",
                "newStart_t",
                "duration",
                "savings",
            ):
                if key not in b:
                    raise LoadShiftInsightError(f"block missing {key}")
            orig = b["originalStart_t"]
            new = b["newStart_t"]
            dur = b["duration"]
            sav = b["savings"]
            if not isinstance(orig, int) or not isinstance(new, int):
                raise LoadShiftInsightError("start slots must be integers")
            if orig < 0 or new < 0 or orig >= total_slots or new >= total_slots:
                raise LoadShiftInsightError("start slots out of range for totalTimeSlots")
            if not isinstance(dur, int) or dur < 1:
                raise LoadShiftInsightError("duration must be a positive integer")
            if orig + dur > total_slots or new + dur > total_slots:
                raise LoadShiftInsightError("block extends past totalTimeSlots")
            if not isinstance(sav, (int, float)):
                raise LoadShiftInsightError("savings must be a number")
            cons = b.get("consumption")
            if cons is not None:
                if not isinstance(cons, list) or len(cons) != dur:
                    raise LoadShiftInsightError("consumption length must equal duration")
            normalized_blocks.append(b)

        if not normalized_blocks:
            raise LoadShiftInsightError(
                f"no valid blockShifts for appId {app_id} (empty or incomplete blocks are skipped)"
            )

        by_fp: dict[tuple[Any, ...], dict[str, Any]] = {}
        for b in normalized_blocks:
            fp = _block_fingerprint(b)
            if fp not in by_fp or float(b["savings"]) > float(by_fp[fp]["savings"]):
                by_fp[fp] = b

        ranked = sorted(by_fp.values(), key=lambda x: float(x["savings"]), reverse=True)

        shift_patterns: list[dict[str, Any]] = []
        for b in ranked:
            shift_patterns.append(
                {
                    "original_window": format_time_window(
                        int(b["originalStart_t"]), int(b["duration"])
                    ),
                    "recommended_window": format_time_window(
                        int(b["newStart_t"]), int(b["duration"])
                    ),
                    "portion_savings": float(b["savings"]),
                }
            )

        pattern_notes = _pattern_notes_from_shift_patterns(shift_patterns)

        appliances_out.append(
            {
                "app_id": app_id,
                "name": _appliance_name(mapping, app_id),
                "monthly_total_savings": float(total_savings),
                "shift_patterns": shift_patterns,
                "pattern_notes": pattern_notes,
            }
        )

    return {
        "currency": currency,
        "granularity_seconds": meta.get("granularity"),
        "total_time_slots": total_slots,
        "analysis_scope": "monthly",
        "appliances": appliances_out,
    }


def _currency_symbol(currency: str) -> str:
    return "₹" if currency == "INR" else f"{currency} "


def _appliance_monthly_savings_positive(app: dict[str, Any]) -> bool:
    return float(app["monthly_total_savings"]) > 0


def _facts_has_any_positive_savings(facts: dict[str, Any]) -> bool:
    return any(_appliance_monthly_savings_positive(a) for a in facts["appliances"])


_REC_SLOT_RE = re.compile(
    r"Day \d+ at (\d{2}:\d{2}) for (\d+) (hour|hours)",
    re.IGNORECASE,
)


def _generalized_cheaper_slot(patterns: list[dict[str, Any]]) -> str | None:
    """
    When every pattern recommends the same window wording, or the same clock+duration on different days,
    return one short phrase for user-facing copy; else None.
    """
    recs: list[str] = []
    for p in patterns:
        w = str(p.get("recommended_window", "")).strip()
        if w:
            recs.append(w)
    if not recs:
        return None
    if len(set(recs)) == 1:
        return recs[0]
    cores: list[tuple[str, str, str]] = []
    for w in recs:
        m = _REC_SLOT_RE.search(w)
        if not m:
            return None
        cores.append((m.group(1), m.group(2), m.group(3).lower()))
    if len(set(cores)) == 1:
        clock, num, label = cores[0]
        return f"{clock} for {num} {label}"
    return None


def _deterministic_one_appliance(app: dict[str, Any], sym: str) -> str:
    """Crisp copy aligned with pattern structure (patterns are savings-sorted)."""
    if not _appliance_monthly_savings_positive(app):
        return ""
    name = app["name"]
    total = float(app["monthly_total_savings"])
    patterns: list[dict[str, Any]] = app.get("shift_patterns") or []
    notes = app["pattern_notes"]
    n = int(notes["shift_event_count"])
    biggest = float(notes["largest_portion_savings"])

    head = f"You could save around {sym}{total:.2f} this month on {name}."
    if not patterns or n < 1:
        return head

    top = patterns[0]
    orig = str(top.get("original_window", "")).strip()
    rec = str(top.get("recommended_window", "")).strip()
    general_rec = _generalized_cheaper_slot(patterns)

    if n == 1:
        return (
            f"{head} Try shifting from {orig} to {rec}—about {sym}{biggest:.2f} of that."
        )

    if general_rec is not None:
        return (
            f"{head} Several changes are suggested; they share a cheaper target around {general_rec} "
            f"instead of peak usage—the largest piece is about {sym}{biggest:.2f}."
        )

    return (
        f"{head} The biggest win ({sym}{biggest:.2f}) is moving from {orig} to {rec}. "
        f"Other suggested shifts cover the rest."
    )


def _deterministic_insights_list(facts: dict[str, Any]) -> list[dict[str, Any]]:
    sym = _currency_symbol(facts["currency"])
    out: list[dict[str, Any]] = []
    for app in facts["appliances"]:
        out.append(
            {
                "appId": app["app_id"],
                "name": app["name"],
                "monthlyTotalSavings": app["monthly_total_savings"],
                "insight": _deterministic_one_appliance(app, sym),
            }
        )
    return out


def _strip_json_fence(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```(?:json)?\s*", "", t, flags=re.IGNORECASE)
        t = re.sub(r"\s*```\s*$", "", t)
    return t.strip()


def _parse_llm_insights_json(text: str) -> dict[str, Any] | None:
    raw = _strip_json_fence(text)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    apps = data.get("appliances")
    if not isinstance(apps, list):
        return None
    return data


def _merge_llm_insights(
    parsed: dict[str, Any], facts: dict[str, Any]
) -> tuple[list[dict[str, Any]], bool]:
    """
    Build per-appliance rows; use model text when present per ``app_id``, else deterministic.
    Returns (rows, all_from_llm).
    """
    sym = _currency_symbol(facts["currency"])
    by_id: dict[int, str] = {}
    for entry in parsed.get("appliances", []):
        if not isinstance(entry, dict):
            continue
        aid = entry.get("app_id")
        ins = entry.get("insight")
        if isinstance(aid, int) and isinstance(ins, str) and ins.strip():
            by_id[aid] = ins.strip()

    rows: list[dict[str, Any]] = []
    all_llm = True
    for app in facts["appliances"]:
        aid = app["app_id"]
        if not _appliance_monthly_savings_positive(app):
            text = ""
        elif aid in by_id:
            text = by_id[aid]
        else:
            text = _deterministic_one_appliance(app, sym)
            all_llm = False
        rows.append(
            {
                "appId": aid,
                "name": app["name"],
                "monthlyTotalSavings": app["monthly_total_savings"],
                "insight": text,
            }
        )
    return rows, all_llm


def _insight_llm_prompt_json(facts: dict[str, Any]) -> str:
    slim_appliances = []
    for a in facts["appliances"]:
        if not _appliance_monthly_savings_positive(a):
            continue
        slim_appliances.append(
            {
                "app_id": a["app_id"],
                "name": a["name"],
                "monthly_total_savings": a["monthly_total_savings"],
                "shift_patterns": a["shift_patterns"],
                "pattern_summary": a["pattern_notes"],
            }
        )
    bundle = {
        "currency": facts["currency"],
        "analysis_scope": facts.get("analysis_scope", "monthly"),
        "total_time_slots_in_period": facts["total_time_slots"],
        "appliances": slim_appliances,
    }
    currency = str(facts.get("currency", ""))
    currency_hint = (
        "For INR use ₹ before amounts (e.g. ₹45.00). For USD use $. "
        "Otherwise prefix with the ISO currency code from the input."
        if currency
        else "Use the currency field from the input for money formatting."
    )
    return f"""You are an energy-savings assistant. Output machine-readable JSON only (no markdown fences).

For EACH appliance, work through this privately (do not print these steps—only output the final JSON):
1) Read shift_patterns and pattern_summary for that appliance.
2) Decide if recommended_window values express ONE recurring idea (same text repeated, or the same clock time and duration on different days). If yes, phrase the insight in general terms (e.g. use the cheaper window around … / shift out of peak into …)—do not list every Day N line by line.
3) If recommended windows genuinely differ, summarize the theme using monthly_total_savings and describe the strongest shift in plain language, still grounded in the JSON.
4) Lead with monthly_total_savings so the user sees the headline saving first.

Rules:
- Use ONLY numbers, names, and times present in the JSON. Do not invent savings or schedules.
- {currency_hint}
- One or two short sentences per appliance, under ~55 words. Sound like a helpful app tip, not a technical report.
- Plain text inside JSON string values only (no markdown).

Required output shape (valid JSON only):
{{"appliances":[{{"app_id":<int>,"insight":"<short text>"}}, ...]}}

Include every app_id from the list below exactly once, in the same order. The list only contains appliances with monthly_total_savings > 0.

Input JSON:
""" + json.dumps(
        bundle, indent=2
    )


def _build_api_response(
    *,
    facts: dict[str, Any],
    appliances_payload: list[dict[str, Any]],
    source: str,
) -> dict[str, Any]:
    meta_out = {
        "granularity": facts.get("granularity_seconds"),
        "totalTimeSlots": facts["total_time_slots"],
        "currency": facts["currency"],
        "analysisScope": facts.get("analysis_scope", "monthly"),
    }
    return {
        "metadata": meta_out,
        "appliances": appliances_payload,
        "source": source,
    }


def _insight_payload_with_facts(
    facts: dict[str, Any],
    *,
    appliances: list[dict[str, Any]],
    source: str,
) -> dict[str, Any]:
    """API body plus embedded ``facts`` (routes may strip ``facts`` per query param)."""
    body = _build_api_response(
        facts=facts, appliances_payload=appliances, source=source
    )
    body["facts"] = facts
    return body


def _deterministic_payload(facts: dict[str, Any]) -> dict[str, Any]:
    appliances = _deterministic_insights_list(facts)
    return _insight_payload_with_facts(
        facts, appliances=appliances, source="deterministic"
    )


def _run_llm_insight_pipeline(
    facts: dict[str, Any],
    *,
    provider_source: str,
    log_label: str,
    generate_text: Callable[[], str],
    import_error_log: str,
) -> dict[str, Any]:
    """
    Call ``generate_text``, parse JSON, merge with facts, fall back to deterministic on failure.
    ``provider_source`` is the response ``source`` when every appliance came from the model.
    """
    try:
        text = generate_text()
        parsed = _parse_llm_insights_json(text)
        if parsed:
            appliances, all_llm = _merge_llm_insights(parsed, facts)
            src = provider_source if all_llm else "deterministic"
            if not all_llm:
                logger.warning(
                    "%s JSON incomplete; filled missing appliances with deterministic text",
                    log_label,
                )
        else:
            logger.warning(
                "%s response was not valid insight JSON; using deterministic", log_label
            )
            appliances = _deterministic_insights_list(facts)
            src = "deterministic"
        return _insight_payload_with_facts(facts, appliances=appliances, source=src)
    except ImportError as e:
        logger.error("%s: %s", import_error_log, e)
    except Exception:
        logger.exception("%s insight generation failed; falling back", log_label)
    return _deterministic_payload(facts)


def _resolve_insight_provider() -> str:
    explicit = (os.environ.get("INSIGHT_LLM_PROVIDER") or "").strip().lower()
    if explicit in ("openai", "gemini"):
        return explicit
    has_gemini = bool((os.environ.get("GEMINI_API_KEY") or "").strip())
    has_openai = bool((os.environ.get("OPENAI_API_KEY") or "").strip())
    if has_gemini:
        return "gemini"
    if has_openai:
        return "openai"
    return "none"


def _openai_client_kwargs(*, api_key: str) -> dict[str, Any]:
    base = (os.environ.get("OPENAI_BASE_URL") or "").strip()
    try:
        timeout = float(os.environ.get("OPENAI_TIMEOUT_SECONDS", "60"))
    except ValueError:
        timeout = 60.0
    if timeout <= 0:
        timeout = 60.0
    try:
        max_retries = int(os.environ.get("OPENAI_MAX_RETRIES", "2"))
    except ValueError:
        max_retries = 2
    if max_retries < 0:
        max_retries = 2
    out: dict[str, Any] = {
        "api_key": api_key,
        "timeout": timeout,
        "max_retries": max_retries,
    }
    if base:
        out["base_url"] = base.rstrip("/")
    return out


def _generate_with_openai(*, api_key: str, model: str, prompt: str) -> str:
    from openai import OpenAI

    client = OpenAI(**_openai_client_kwargs(api_key=api_key))
    completion = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=2048,
        temperature=0.4,
        response_format={"type": "json_object"},
    )
    choice = completion.choices[0].message
    text = (choice.content or "").strip()
    if not text:
        raise RuntimeError("empty OpenAI model response")
    return text


def _generate_with_gemini(*, api_key: str, model: str, prompt: str) -> str:
    from google import genai

    client = genai.Client(api_key=api_key)
    response = client.models.generate_content(model=model, contents=prompt)
    text = (response.text or "").strip()
    if not text:
        raise RuntimeError("empty Gemini model response")
    return text


class LoadShiftInsightService:
    """Generate narrative insights from a load-shift API response."""

    def __init__(
        self,
        *,
        mapping_path: Path | None = None,
        model: str | None = None,
        openai_model: str | None = None,
    ) -> None:
        self._mapping = _load_appliance_mapping(mapping_path)
        self._gemini_model = model or os.environ.get("GEMINI_MODEL", "gemini-2.0-flash")
        self._openai_model = openai_model or os.environ.get("OPENAI_MODEL", "gpt-4o-mini")

    def build_facts(self, payload: dict[str, Any]) -> dict[str, Any]:
        return build_insight_facts(payload, mapping=self._mapping)

    def generate_insight(self, payload: dict[str, Any]) -> dict[str, Any]:
        """
        Return API-shaped dict with ``metadata``, ``appliances`` (``insight`` text only
        if ``monthlyTotalSavings`` > 0; else ``""``), ``source``, and optional ``facts``.
        """
        facts = self.build_facts(payload)
        if not _facts_has_any_positive_savings(facts):
            return _deterministic_payload(facts)

        provider = _resolve_insight_provider()
        prompt = _insight_llm_prompt_json(facts)

        if provider == "none":
            logger.warning(
                "No GEMINI_API_KEY or OPENAI_API_KEY set; using deterministic insights"
            )
            return _deterministic_payload(facts)

        if provider == "openai":
            api_key = (os.environ.get("OPENAI_API_KEY") or "").strip()
            if not api_key:
                logger.warning(
                    "INSIGHT_LLM_PROVIDER=openai but OPENAI_API_KEY missing; using deterministic"
                )
                return _deterministic_payload(facts)
            return _run_llm_insight_pipeline(
                facts,
                provider_source="openai",
                log_label="OpenAI",
                generate_text=lambda: _generate_with_openai(
                    api_key=api_key, model=self._openai_model, prompt=prompt
                ),
                import_error_log="openai package not installed",
            )

        api_key = (os.environ.get("GEMINI_API_KEY") or "").strip()
        if not api_key:
            logger.warning(
                "INSIGHT_LLM_PROVIDER=gemini but GEMINI_API_KEY missing; using deterministic"
            )
            return _deterministic_payload(facts)
        return _run_llm_insight_pipeline(
            facts,
            provider_source="gemini",
            log_label="Gemini",
            generate_text=lambda: _generate_with_gemini(
                api_key=api_key, model=self._gemini_model, prompt=prompt
            ),
            import_error_log="google-genai not installed",
        )
