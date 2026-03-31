import json
import logging
import os
from pathlib import Path
from typing import Optional

import requests
from flask import Flask, Response, jsonify, render_template, request
from pydantic import BaseModel, Field, ValidationError

from constraint_analyzer import analyze_constraint_text
from pipeline_build_merged import (
    build_merged_for_uuid,
    dashboard_usage_config,
    fetch_latest_bill_cycle_row_from_usage_chart,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
SHIFTABLE_APPLIANCE_IDS = [18, 2, 3, 4, 7, 30]
# Preserve insertion order in JSON output (avoid alphabetical sorting).
app.json.sort_keys = False


def _load_appliance_catalog() -> dict[int, str]:
    mapping_path = Path(__file__).with_name("appliance_mapping.json")
    raw = mapping_path.read_text(encoding="utf-8")
    data = json.loads(raw)
    return {int(k): str(v) for k, v in data.items()}


APPLIANCE_CATALOG = _load_appliance_catalog()


class AllowedWindow(BaseModel):
    startHour: int = Field(ge=0, le=24)
    endHour: int = Field(ge=0, le=24)


class BlockConstraints(BaseModel):
    maxShiftHours: Optional[int] = Field(default=None, ge=0)
    allowedWindows: Optional[list[AllowedWindow]] = None


class ApplianceTimeConstraint(BaseModel):
    appliance_id: int
    load_start_time: str
    load_end_time: str


class AnalyzeConstraintRequest(BaseModel):
    constraintText: Optional[str] = None
    constraints: Optional[list[ApplianceTimeConstraint]] = None


class ApplianceConstraint(BaseModel):
    applianceId: int
    blockConstraints: BlockConstraints


class AnalyzeConstraintResponse(BaseModel):
    applianceConstraints: list[ApplianceConstraint]


def _time_to_hour(value: str) -> int:
    text = value.strip()
    if ":" in text:
        hour_part = text.split(":", maxsplit=1)[0]
        hour = int(hour_part)
    else:
        hour = int(text)
    if hour < 0 or hour > 24:
        raise ValueError("Hour out of range")
    return hour


def _constraints_from_payload(
    constraints: Optional[list[ApplianceTimeConstraint]],
    *,
    shiftable_appliance_ids: list[int],
) -> dict[int, dict[str, object]]:
    if not constraints:
        return {}
    normalized: dict[int, dict[str, object]] = {}
    for block in constraints:
        if block.appliance_id not in shiftable_appliance_ids:
            continue
        start_hour = _time_to_hour(block.load_start_time)
        end_hour = _time_to_hour(block.load_end_time)
        normalized[block.appliance_id] = {
            "maxShiftHours": None,
            "allowedWindows": [{"startHour": start_hour, "endHour": end_hour}],
        }
    return normalized


@app.after_request
def add_cors_headers(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    return response


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/health")
def health():
    return {"status": "ok"}


@app.post("/api/build-merged")
def build_merged():
    """
    Build merged_rates_appliances payload for a UUID.

    Body JSON:
      { "uuid": "<uuid>", "timezone": "UTC" }
      Optional: { "userUuid": "<userUuid>" } if dashboard UUID differs from S3 UUID.
    """
    body = request.get_json(silent=True) or {}
    uuid = (body.get("uuid") or "").strip()
    user_uuid = (body.get("userUuid") or "").strip() or None
    timezone = (body.get("timezone") or "UTC").strip()
    if not uuid:
        return {"error": "uuid is required"}, 400

    try:
        merged = build_merged_for_uuid(
            uuid,
            user_uuid=user_uuid,
            out_dir=Path(__file__).resolve().parent / "docs",
            shiftable_ids=set(SHIFTABLE_APPLIANCE_IDS),
            timezone=timezone,
        )
    except Exception as e:  # noqa: BLE001
        logger.exception("build_merged failed")
        return {"error": str(e)}, 500

    # Ensure key order is preserved in the response text.
    return Response(json.dumps(merged, ensure_ascii=False), mimetype="application/json")


@app.post("/api/build-merged-optimize")
def build_merged_optimize():
    """
    Build merged payload then call local optimizer FastAPI.

    Optimizer URL: http://127.0.0.1:8000/optimize
    """
    body = request.get_json(silent=True) or {}
    uuid = (body.get("uuid") or "").strip()
    user_uuid = (body.get("userUuid") or "").strip() or None
    timezone = (body.get("timezone") or "UTC").strip()
    if not uuid:
        return {"error": "uuid is required"}, 400

    try:
        merged = build_merged_for_uuid(
            uuid,
            user_uuid=user_uuid,
            out_dir=Path(__file__).resolve().parent / "docs",
            shiftable_ids=set(SHIFTABLE_APPLIANCE_IDS),
            timezone=timezone,
        )
    except Exception as e:  # noqa: BLE001
        logger.exception("build_merged_optimize: build_merged_for_uuid failed")
        return {"error": str(e)}, 500

    # Fetch current bill-cycle totals + appliance breakdown from dashboard (latest bill cycle).
    try:
        cfg = dashboard_usage_config()
        dashboard_user_uuid = user_uuid or uuid
        latest_row = fetch_latest_bill_cycle_row_from_usage_chart(dashboard_user_uuid, cfg)
    except Exception as e:  # noqa: BLE001
        logger.exception("build_merged_optimize: dashboard usage fetch failed")
        return {"error": str(e)}, 502

    current_total_cost = float(latest_row.get("cost") or 0.0)
    current_total_consumption = float(latest_row.get("consumption") or 0.0)
    itemizations = latest_row.get("itemizationDetailsList") or []

    # Per your clarification: dashboard itemizationDetailsList[].id is the appliance appId.
    # We treat "usage" as consumption (same unit as dashboard "consumption").
    current_by_app: dict[int, dict[str, float]] = {
        app_id: {"cost": 0.0, "consumption": 0.0} for app_id in SHIFTABLE_APPLIANCE_IDS
    }
    if isinstance(itemizations, list):
        for it in itemizations:
            if not isinstance(it, dict):
                continue
            if it.get("id") is None:
                continue
            try:
                app_id = int(it["id"])
            except Exception:
                continue
            if app_id not in SHIFTABLE_APPLIANCE_IDS:
                continue
            current_by_app[app_id] = {
                "cost": float(it.get("cost") or 0.0),
                "consumption": float(it.get("usage") or 0.0),
            }

    try:
        opt_resp = requests.post(
            "http://127.0.0.1:8000/optimize",
            json=merged,
            timeout=300,
        )
        opt_resp.raise_for_status()
        opt_json = opt_resp.json()
    except Exception as e:  # noqa: BLE001
        logger.exception("build_merged_optimize: optimizer call failed")
        return {"error": str(e)}, 502

    def _extract_savings_by_app(obj: object) -> dict[int, dict[str, float]]:
        out: dict[int, dict[str, float]] = {}

        def add(app_id: int, *, cost: float = 0.0, cons: float = 0.0) -> None:
            if app_id not in out:
                out[app_id] = {"costSavings": 0.0, "consumptionSavings": 0.0}
            out[app_id]["costSavings"] += float(cost or 0.0)
            out[app_id]["consumptionSavings"] += float(cons or 0.0)

        # Optimizer response format we have:
        # { metadata: {...}, loadShift: [ {appId, totalSavings, blockShifts:[{savings,...}, ...]}, ... ] }
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

    savings_by_app = _extract_savings_by_app(opt_json)

    appliances_out = []
    total_shiftable_cost_savings = 0.0
    total_shiftable_consumption_savings = 0.0
    for app_id in SHIFTABLE_APPLIANCE_IDS:
        cur = current_by_app.get(app_id, {"cost": 0.0, "consumption": 0.0})
        sav = savings_by_app.get(app_id, {"costSavings": 0.0, "consumptionSavings": 0.0})
        # We only have reliable savings per appliance from optimizer, but not current per appliance.
        # Avoid negative numbers for per-appliance "best".
        best_cost = max(0.0, cur["cost"] - float(sav["costSavings"] or 0.0))
        # Load shifting changes timing, not energy consumed. Keep consumption unchanged unless
        # an optimizer explicitly returns kWh savings.
        best_cons = cur["consumption"]
        appliances_out.append(
            {
                "appId": app_id,
                "name": APPLIANCE_CATALOG.get(app_id, f"UNKNOWN_{app_id}"),
                "current": cur,
                "savings": sav,
                "best": {"cost": best_cost, "consumption": best_cons},
            }
        )
        total_shiftable_cost_savings += float(sav["costSavings"] or 0.0)
        total_shiftable_consumption_savings += float(sav["consumptionSavings"] or 0.0)

    final = {
        "billCycle": {
            "intervalStart": latest_row.get("intervalStart"),
            "intervalEnd": latest_row.get("intervalEnd"),
        },
        "total": {
            "current": {"cost": current_total_cost, "consumption": current_total_consumption},
            "shiftableSavings": {
                "costSavings": total_shiftable_cost_savings,
                "consumptionSavings": total_shiftable_consumption_savings,
            },
            "best": {
                "cost": max(0.0, current_total_cost - total_shiftable_cost_savings),
                "consumption": current_total_consumption,
            },
        },
        "appliances": appliances_out,
        "note": (
            "Dashboard itemizationDetailsList[].id is treated as appliance appId; "
            "itemizationDetailsList[].usage is treated as appliance consumption. "
            "Optimizer savings are applied to totals; per-appliance best is clamped >= 0. "
            "Consumption does not decrease with load shifting."
        ),
    }

    return Response(json.dumps(final, ensure_ascii=False), mimetype="application/json")


@app.route("/analyze-constraint", methods=["POST", "OPTIONS"])
def analyze_constraint():
    if request.method == "OPTIONS":
        return ("", 204)

    try:
        payload = AnalyzeConstraintRequest.model_validate(request.get_json(silent=True) or {})
    except ValidationError as exc:
        return jsonify({"error": "Invalid request", "details": exc.errors()}), 400

    if not (payload.constraintText and payload.constraintText.strip()) and not payload.constraints:
        return jsonify({"error": "Invalid request", "details": "Provide constraintText or constraints"}), 400

    try:
        payload_constraints = _constraints_from_payload(
            payload.constraints,
            shiftable_appliance_ids=SHIFTABLE_APPLIANCE_IDS,
        )
    except ValueError as exc:
        return jsonify({"error": "Invalid request", "details": str(exc)}), 400

    try:
        text_constraints: dict[int, dict[str, object]] = {}
        if payload.constraintText and payload.constraintText.strip():
            text_result = analyze_constraint_text(
                payload.constraintText.strip(),
                shiftable_appliance_ids=SHIFTABLE_APPLIANCE_IDS,
                appliance_catalog=APPLIANCE_CATALOG,
            )
            text_constraints = text_result.appliance_constraints

        # Text-derived constraints take precedence for the same appliance id.
        merged_constraints = dict(payload_constraints)
        merged_constraints.update(text_constraints)

        appliance_constraints: list[ApplianceConstraint] = []
        for appliance_id, block in merged_constraints.items():
            appliance_constraints.append(
                ApplianceConstraint(
                    applianceId=appliance_id,
                    blockConstraints=BlockConstraints(
                        maxShiftHours=block.get("maxShiftHours"),
                        allowedWindows=block.get("allowedWindows"),
                    ),
                )
            )
        response = AnalyzeConstraintResponse(
            applianceConstraints=appliance_constraints,
        )
    except Exception:
        logger.exception("Failed to analyze constraint")
        return jsonify({"error": "Failed to analyze constraint"}), 500

    return jsonify(response.model_dump(mode="json"))


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5001"))
    logger.info("Starting Flask on http://127.0.0.1:%s", port)
    app.run(debug=True, host="127.0.0.1", port=port)
