"""
Shared helpers for merged payload → /optimize → insights (build-merged-optimize + load-shift uuid mode).
"""

from __future__ import annotations

import logging
import os
from typing import Any

SHIFTABLE_APPLIANCE_IDS: list[int] = [18, 2, 3, 4, 7]

OPTIMIZER_OPTIMIZE_URL = (
    os.environ.get("OPTIMIZER_OPTIMIZE_URL") or "http://127.0.0.1:8000/optimize"
).strip()


def log_optimizer_http_response(logger: logging.Logger, opt_resp: Any) -> None:
    """Log status, URL, and body text (truncated) from the optimizer HTTP response."""
    try:
        body = opt_resp.text
    except Exception as e:  # noqa: BLE001
        logger.warning("optimizer response: could not read body: %s", e)
        body = ""
    max_len = 16000
    if len(body) > max_len:
        body = f"{body[:max_len]}... [truncated, total {len(body)} chars]"
    logger.info(
        "optimizer response status=%s url=%s body=%s",
        getattr(opt_resp, "status_code", "?"),
        getattr(opt_resp, "url", ""),
        body,
    )


def extract_savings_by_app(obj: object) -> dict[int, dict[str, float]]:
    out: dict[int, dict[str, float]] = {}

    def add(app_id: int, *, cost: float = 0.0, cons: float = 0.0) -> None:
        if app_id not in out:
            out[app_id] = {"costSavings": 0.0, "consumptionSavings": 0.0}
        out[app_id]["costSavings"] += float(cost or 0.0)
        out[app_id]["consumptionSavings"] += float(cons or 0.0)

    if isinstance(obj, dict) and isinstance(obj.get("loadShift"), list):
        for it in obj.get("loadShift") or []:
            if not isinstance(it, dict):
                continue
            if it.get("appId") is None:
                continue
            try:
                app_id = int(it["appId"])
            except Exception:
                continue

            total_s = it.get("totalSavings")
            if total_s is None:
                total_s = 0.0
                for bs in it.get("blockShifts") or []:
                    if isinstance(bs, dict) and bs.get("savings") is not None:
                        try:
                            total_s += float(bs["savings"])
                        except Exception:
                            pass
            add(app_id, cost=float(total_s or 0.0), cons=0.0)
        return out

    def walk(x: object) -> None:
        if isinstance(x, dict):
            app_id = None
            if "appId" in x and x["appId"] is not None:
                try:
                    app_id = int(x["appId"])
                except Exception:
                    app_id = None
            if app_id is not None:
                cost_s = (
                    x.get("costSavings")
                    or x.get("savingsCost")
                    or x.get("savings_cost")
                    or x.get("savings_cost_inr")
                    or 0.0
                )
                cons_s = (
                    x.get("consumptionSavings")
                    or x.get("savingsConsumption")
                    or x.get("savings_consumption")
                    or 0.0
                )
                if not cost_s and not cons_s and x.get("savings") is not None:
                    cost_s = x.get("savings")
                add(app_id, cost=float(cost_s or 0.0), cons=float(cons_s or 0.0))

            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for it in x:
                walk(it)

    walk(obj)
    return out


def current_by_app_from_latest_row(
    latest_row: dict[str, Any],
    *,
    shiftable_ids: list[int] | None = None,
) -> dict[int, dict[str, float]]:
    """
    Dashboard itemization keyed by appId: cost + consumption (usage), for shiftable ids only.
    """
    ids = shiftable_ids if shiftable_ids is not None else SHIFTABLE_APPLIANCE_IDS
    current_by_app: dict[int, dict[str, float]] = {
        app_id: {"cost": 0.0, "consumption": 0.0} for app_id in ids
    }
    itemizations = latest_row.get("itemizationDetailsList") or []
    if isinstance(itemizations, list):
        for it in itemizations:
            if not isinstance(it, dict) or it.get("id") is None:
                continue
            try:
                app_id = int(it["id"])
            except Exception:
                continue
            if app_id not in current_by_app:
                continue
            current_by_app[app_id] = {
                "cost": float(it.get("cost") or 0.0),
                "consumption": float(it.get("usage") or 0.0),
            }
    return current_by_app


def bill_cost_by_app_from_latest_row(
    latest_row: dict[str, Any],
    *,
    shiftable_ids: list[int] | None = None,
) -> dict[int, float]:
    """Map appId -> dashboard bill cost (for bill-share insights)."""
    cur = current_by_app_from_latest_row(latest_row, shiftable_ids=shiftable_ids)
    return {app_id: float(v.get("cost") or 0.0) for app_id, v in cur.items()}
