"""
Per-appliance insight strings for /api/build-merged-optimize.

Combines dashboard bill-share percent (costSavings / current cost) with
load-shift timing from the optimizer payload (via build_insight_facts).
"""

from __future__ import annotations

import logging
from typing import Any

from .load_shift_insights import (
    LoadShiftInsightError,
    appliance_timing_clause,
    build_insight_facts,
)

logger = logging.getLogger(__name__)


def _format_bill_share_percent(cost_savings: float, current_cost: float) -> str | None:
    if current_cost <= 0 or cost_savings <= 0:
        return None
    pct = (float(cost_savings) / float(current_cost)) * 100.0
    rounded = round(pct, 4)
    return f"{rounded:.4f}".rstrip("0").rstrip(".")


def format_appliance_bill_share_insight(
    *,
    current_cost: float,
    cost_savings: float,
    facts_row: dict[str, Any] | None,
    friendly_fallback: str,
) -> str:
    """Same sentence shape as /api/build-merged-optimize per-appliance insight."""
    pct_str = _format_bill_share_percent(cost_savings, current_cost)
    if pct_str is None:
        return ""
    clause = appliance_timing_clause(facts_row) if facts_row else ""
    if not clause:
        clause = f"shifting {friendly_fallback} usage to lower-cost times"
    return f"You can save {pct_str}% by {clause}."


def apply_bill_share_to_load_shift_response(
    result: dict[str, Any],
    facts: dict[str, Any],
    bill_cost_by_app: dict[int, float],
) -> None:
    """
    Overwrite ``insight`` on each appliance when bill cost is known, using optimizer
    savings from facts (monthly_total_savings) and ``appliance_timing_clause``.
    """
    facts_by_id: dict[int, dict[str, Any]] = {}
    for row in facts.get("appliances") or []:
        if not isinstance(row, dict):
            continue
        aid = row.get("app_id")
        if isinstance(aid, int):
            facts_by_id[aid] = row

    for row in result.get("appliances") or []:
        if not isinstance(row, dict):
            continue
        aid = row.get("appId")
        if not isinstance(aid, int):
            continue
        if aid not in bill_cost_by_app:
            continue
        cur_cost = float(bill_cost_by_app[aid])
        fr = facts_by_id.get(aid)
        if not fr:
            continue
        sav = float(fr.get("monthly_total_savings") or 0.0)
        friendly = _friendly_name_from_facts_row(fr)
        text = format_appliance_bill_share_insight(
            current_cost=cur_cost,
            cost_savings=sav,
            facts_row=fr,
            friendly_fallback=friendly,
        )
        if text:
            row["insight"] = text
        else:
            row["insight"] = ""


def _friendly_name_from_facts_row(fr: dict[str, Any]) -> str:
    return str(fr.get("name", "appliance")).replace("_", " ").lower()


def build_insight_by_app_id(
    *,
    opt_json: dict[str, Any],
    current_by_app: dict[int, dict[str, float]],
    savings_by_app: dict[int, dict[str, float]],
    shiftable_app_ids: list[int],
    appliance_catalog: dict[int, str],
) -> dict[int, str]:
    """
    Map appId -> insight text (empty when no bill-share savings to describe).

    Percent uses the same current cost and optimizer cost savings as the API payload:
    (savings.costSavings / current.cost) * 100, rounded to 4 decimals then trimmed.
    """
    facts_by_app: dict[int, dict[str, Any]] = {}
    try:
        facts = build_insight_facts(opt_json)
        for row in facts.get("appliances") or []:
            if not isinstance(row, dict):
                continue
            aid = row.get("app_id")
            if isinstance(aid, int):
                facts_by_app[aid] = row
    except LoadShiftInsightError as e:
        logger.info("merged optimize insights: timing facts unavailable (%s)", e)

    out: dict[int, str] = {}
    for app_id in shiftable_app_ids:
        cur = current_by_app.get(app_id, {"cost": 0.0, "consumption": 0.0})
        sav = savings_by_app.get(
            app_id, {"costSavings": 0.0, "consumptionSavings": 0.0}
        )
        cur_cost = float(cur.get("cost") or 0.0)
        cost_sav = float(sav.get("costSavings") or 0.0)
        pct_str = _format_bill_share_percent(cost_sav, cur_cost)
        if pct_str is None:
            out[app_id] = ""
            continue

        canon = appliance_catalog.get(app_id, f"UNKNOWN_{app_id}")
        friendly = str(canon).replace("_", " ").lower()
        row = facts_by_app.get(app_id)
        out[app_id] = format_appliance_bill_share_insight(
            current_cost=cur_cost,
            cost_savings=cost_sav,
            facts_row=row,
            friendly_fallback=friendly,
        )
    return out
