"""
Build grounded facts from load-shift API payloads and generate user-facing insights via Gemini.

Requires GEMINI_API_KEY in the environment for LLM output; falls back to deterministic text if unset.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

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


def build_insight_facts(
    payload: dict[str, Any],
    *,
    mapping: dict[str, str] | None = None,
    max_shifts_per_appliance: int = 2,
) -> dict[str, Any]:
    """
    Normalize API payload into facts the LLM must not contradict.

    Picks up to ``max_shifts_per_appliance`` block shifts per appliance by largest ``savings``,
    deduped by (originalStart_t, newStart_t, duration).
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

        # Dedupe by shift pattern, keep best savings per pattern
        by_fp: dict[tuple[Any, ...], dict[str, Any]] = {}
        for b in normalized_blocks:
            fp = _block_fingerprint(b)
            if fp not in by_fp or float(b["savings"]) > float(by_fp[fp]["savings"]):
                by_fp[fp] = b

        ranked = sorted(by_fp.values(), key=lambda x: float(x["savings"]), reverse=True)[
            :max_shifts_per_appliance
        ]

        shift_examples = []
        for b in ranked:
            shift_examples.append(
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

        appliances_out.append(
            {
                "app_id": app_id,
                "name": _appliance_name(mapping, app_id),
                "period_total_savings": float(total_savings),
                "shift_examples": shift_examples,
            }
        )

    return {
        "currency": currency,
        "granularity_seconds": meta.get("granularity"),
        "total_time_slots": total_slots,
        "appliances": appliances_out,
    }


def _deterministic_insight(facts: dict[str, Any]) -> str:
    """Template fallback when Gemini is unavailable."""
    lines: list[str] = []
    cur = facts["currency"]
    sym = "₹" if cur == "INR" else f"{cur} "
    for app in facts["appliances"]:
        name = app["name"]
        total = app["period_total_savings"]
        lines.append(
            f"This period you could save about {sym}{total:.2f} on {name} by moving usage to cheaper times."
        )
        for ex in app["shift_examples"]:
            lines.append(
                f"  • Use {name} during {ex['recommended_window']} instead of "
                f"{ex['original_window']} (about {sym}{ex['portion_savings']:.2f} toward this period's savings)."
            )
    return "\n".join(lines)


def _gemini_prompt(facts: dict[str, Any]) -> str:
    return """You are an energy-savings assistant. Write short, friendly insights for a household user.

Rules:
- Use ONLY the numbers, appliance names, and time windows in the JSON facts. Do not invent amounts or times.
- Frame savings as applying to this analysis period (not hourly billing jargon).
- Lead with the period total savings (period_total_savings) and currency for each appliance.
- Add at most one sentence per entry in shift_examples, describing moving from original_window to recommended_window.
- Use user-facing day numbers (Day 1 = first day) exactly as given in the window strings.
- Keep total output under 200 words. Plain text only, no markdown headings.

Facts JSON:
""" + json.dumps(
        facts, indent=2
    )


class LoadShiftInsightService:
    """Generate narrative insights from a load-shift API response."""

    def __init__(
        self,
        *,
        mapping_path: Path | None = None,
        model: str | None = None,
    ) -> None:
        self._mapping = _load_appliance_mapping(mapping_path)
        self._model = model or os.environ.get("GEMINI_MODEL")

    def build_facts(self, payload: dict[str, Any]) -> dict[str, Any]:
        return build_insight_facts(payload, mapping=self._mapping)

    def generate_insight(self, payload: dict[str, Any]) -> dict[str, Any]:
        """
        Return ``{"insight": str, "source": "gemini"|"deterministic", "facts": ...}``.
        """
        facts = self.build_facts(payload)
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            logger.warning("GEMINI_API_KEY not set; using deterministic insights")
            return {
                "insight": _deterministic_insight(facts),
                "source": "deterministic",
                "facts": facts,
            }

        try:
            from google import genai
        except ImportError as e:
            logger.error("google-genai not installed: %s", e)
            return {
                "insight": _deterministic_insight(facts),
                "source": "deterministic",
                "facts": facts,
            }

        try:
            client = genai.Client(api_key=api_key)
            response = client.models.generate_content(
                model=self._model,
                contents=_gemini_prompt(facts),
            )
            text = (response.text or "").strip()
            if not text:
                raise RuntimeError("empty model response")
            return {"insight": text, "source": "gemini", "facts": facts}
        except Exception:
            logger.exception("Gemini insight generation failed; falling back")
            return {
                "insight": _deterministic_insight(facts),
                "source": "deterministic",
                "facts": facts,
            }
