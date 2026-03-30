"""HTTP routes for load-shift insights (body comes from an external load-shift service)."""

from __future__ import annotations

import logging
from typing import Optional

from flask import Blueprint, jsonify, request

from .load_shift_insights import LoadShiftInsightError, LoadShiftInsightService

logger = logging.getLogger(__name__)

insights_bp = Blueprint("insights", __name__, url_prefix="/api")

_service: Optional[LoadShiftInsightService] = None


def _get_insight_service() -> LoadShiftInsightService:
    global _service
    if _service is None:
        _service = LoadShiftInsightService()
    return _service


@insights_bp.route("/insights/load-shift", methods=["POST"])
def load_shift_insights():
    """
    POST body: JSON from the external load-shift API.

    Query ``include_facts=1`` to include structured facts used for prompting.
    """
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify({"error": "Expected JSON object body"}), 400

    try:
        result = _get_insight_service().generate_insight(body)
    except LoadShiftInsightError as e:
        logger.info("load-shift validation failed: %s", e)
        return jsonify({"error": str(e)}), 400

    if request.args.get("include_facts") not in ("1", "true", "yes"):
        result = {k: v for k, v in result.items() if k != "facts"}

    return jsonify(result)
