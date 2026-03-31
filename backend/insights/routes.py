"""HTTP routes for load-shift insights (body comes from an external load-shift service)."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

import requests
from flask import Blueprint, jsonify, request

from pipeline_build_merged import (
    build_merged_for_uuid,
    dashboard_usage_config,
    fetch_latest_bill_cycle_row_from_usage_chart,
)

from optimize_insights_common import (
    OPTIMIZER_OPTIMIZE_URL,
    SHIFTABLE_APPLIANCE_IDS,
    bill_cost_by_app_from_latest_row,
    log_optimizer_http_response,
)

from .load_shift_insights import LoadShiftInsightError, LoadShiftInsightService

logger = logging.getLogger(__name__)

insights_bp = Blueprint("insights", __name__, url_prefix="/api")

_service: Optional[LoadShiftInsightService] = None

_BACKEND_DIR = Path(__file__).resolve().parent.parent


def _get_insight_service() -> LoadShiftInsightService:
    global _service
    if _service is None:
        _service = LoadShiftInsightService()
    return _service


def _split_payload_and_bill_costs(
    body: dict[str, Any],
) -> tuple[dict[str, Any], dict[int, float] | None]:
    """
    Optional ``currentCostByApp``: map of appliance id to dashboard bill cost for the
    same period as ``/api/build-merged-optimize`` itemization ``cost``.
    """
    raw = body.get("currentCostByApp")
    if not isinstance(raw, dict):
        return body, None
    costs: dict[int, float] = {}
    for k, v in raw.items():
        try:
            aid = int(k)
            costs[aid] = float(v)
        except (TypeError, ValueError):
            continue
    payload = {k: v for k, v in body.items() if k != "currentCostByApp"}
    return payload, costs if costs else None


def _run_insights_uuid_mode(body: dict[str, Any]) -> tuple[dict[str, Any] | None, tuple[int, str] | None]:
    """
    Same pipeline as ``/api/build-merged-optimize`` (merged → optimize → facts) with
    dashboard bill costs for bill-share insights. Returns (result_json, (http_code, err)).
    """
    uuid = (body.get("uuid") or "").strip()
    user_uuid = (body.get("userUuid") or "").strip() or None
    timezone = (body.get("timezone") or "UTC").strip()

    try:
        merged = build_merged_for_uuid(
            uuid,
            user_uuid=user_uuid,
            out_dir=_BACKEND_DIR / "docs",
            shiftable_ids=set(SHIFTABLE_APPLIANCE_IDS),
            timezone=timezone,
        )
    except Exception as e:  # noqa: BLE001
        logger.exception("load-shift uuid mode: build_merged_for_uuid failed")
        return None, (502, str(e))

    try:
        cfg = dashboard_usage_config()
        dashboard_user_uuid = user_uuid or uuid
        latest_row = fetch_latest_bill_cycle_row_from_usage_chart(dashboard_user_uuid, cfg)
    except Exception as e:  # noqa: BLE001
        logger.exception("load-shift uuid mode: dashboard usage fetch failed")
        return None, (502, str(e))

    bill_costs = bill_cost_by_app_from_latest_row(
        latest_row,
        shiftable_ids=SHIFTABLE_APPLIANCE_IDS,
    )

    try:
        opt_resp = requests.post(
            OPTIMIZER_OPTIMIZE_URL,
            json=merged,
            timeout=300,
        )
        log_optimizer_http_response(logger, opt_resp)
        opt_resp.raise_for_status()
        opt_json = opt_resp.json()
    except Exception as e:  # noqa: BLE001
        logger.exception("load-shift uuid mode: optimizer call failed")
        return None, (502, str(e))

    if not isinstance(opt_json, dict):
        return None, (502, "optimizer returned non-object JSON")

    try:
        result = _get_insight_service().generate_insight(
            opt_json,
            bill_cost_by_app=bill_costs,
            bill_share_only=True,
        )
    except LoadShiftInsightError as e:
        logger.info("load-shift uuid mode: fact validation failed: %s", e)
        return None, (400, str(e))

    return result, None


@insights_bp.route("/insights/load-shift", methods=["POST"])
def load_shift_insights():
    """
    Two modes (same bill-share + timing insights as ``/api/build-merged-optimize`` when
    costs are known):

    1) **UUID mode** — ``{ "uuid": "...", "timezone": "UTC", "userUuid": optional }``:
       builds merged payload, calls ``/optimize``, loads dashboard itemization costs,
       returns load-shift-shaped JSON with ``source``: ``bill-share``.

    2) **Payload mode** — raw optimizer JSON (``metadata``, ``loadShift``, …). Optional
       ``currentCostByApp`` for dashboard costs; when present, uses ``bill-share`` style
       and skips LLM. Without it, uses LLM/deterministic narrative from facts.

    Response: ``metadata``, ``appliances``, ``source``, optional ``facts`` when
    ``include_facts=1``.
    """
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify({"error": "Expected JSON object body"}), 400

    if (body.get("uuid") or "").strip():
        result, err = _run_insights_uuid_mode(body)
        if err is not None:
            code, msg = err
            return jsonify({"error": msg}), code
        assert result is not None
    else:
        payload, bill_costs = _split_payload_and_bill_costs(body)
        use_bill_share_only = bool(bill_costs)
        try:
            result = _get_insight_service().generate_insight(
                payload,
                bill_cost_by_app=bill_costs,
                bill_share_only=use_bill_share_only,
            )
        except LoadShiftInsightError as e:
            logger.info("load-shift validation failed: %s", e)
            return jsonify({"error": str(e)}), 400

    if request.args.get("include_facts") not in ("1", "true", "yes"):
        result = {k: v for k, v in result.items() if k != "facts"}

    return jsonify(result)
