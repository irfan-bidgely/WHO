import json
import logging
from pathlib import Path
from typing import Optional

from flask import Flask, jsonify, render_template, request
from pydantic import BaseModel, Field, ValidationError

from constraint_analyzer import analyze_constraint_text

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
SHIFTABLE_APPLIANCE_IDS = [18, 2, 3, 4, 7, 30]


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
    logger.info("Starting Flask on http://127.0.0.1:5000")
    app.run(debug=True, host="127.0.0.1", port=5000)
