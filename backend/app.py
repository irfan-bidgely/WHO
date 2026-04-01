import json
import logging
import os
from copy import deepcopy
from pathlib import Path
import concurrent.futures

from dotenv import load_dotenv
from typing import Optional, Tuple

import requests
from flask import Flask, Response, jsonify, render_template, request
from pydantic import BaseModel, Field, ValidationError

from constraint_analyzer import (
    analyze_constraint_text,
    filter_constraints_to_inferred_appliances,
    half_open_span_hours_from_windows,
    merge_fallback_where_windows_missing,
)
from pipeline_build_merged import (
    build_merged_for_uuid,
    dashboard_usage_config,
    fetch_latest_bill_cycle_row_from_usage_chart,
)

from insights import LoadShiftInsightService, build_insight_by_app_id, insights_bp
from optimize_insights_common import (
    OPTIMIZER_OPTIMIZE_URL,
    SHIFTABLE_APPLIANCE_IDS,
    current_by_app_from_latest_row,
    bill_cost_by_app_from_latest_row,
    extract_savings_by_app,
    log_optimizer_http_response,
)

load_dotenv(Path(__file__).resolve().parent / ".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

app.register_blueprint(insights_bp)
# Preserve insertion order in JSON output (avoid alphabetical sorting).
app.json.sort_keys = False


def _load_backend_dotenv() -> None:
    """Load key/value pairs from backend/.env when running python app.py directly."""
    env_path = Path(__file__).with_name(".env")
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", maxsplit=1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


_load_backend_dotenv()


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
    halfOpenSpanHours: Optional[int] = Field(
        default=None,
        ge=0,
        description="Max single-window span [start,end) in hours; optimizers use narrower normalized hours.",
    )


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


def _window_bounds_from_dict(w: dict) -> Optional[Tuple[int, int]]:
    """Read start/end hour from camelCase or snake_case keys."""
    try:
        if "startHour" in w and "endHour" in w:
            return int(w["startHour"]), int(w["endHour"])
        if "start_hour" in w and "end_hour" in w:
            return int(w["start_hour"]), int(w["end_hour"])
    except (TypeError, ValueError):
        return None
    return None


def _normalize_allowed_windows_for_optimizer(windows: object) -> list[dict[str, int]]:
    """
    Local optimizers often validate endHour in 0..23 only. User-facing times like
    24:00 (midnight) become endHour 24 in our dict and trigger HTTP 400.
    Map endHour 24 -> 23 (last clock hour of the day); clamp to 0..23.

    Some optimizers reject startHour == endHour; use at least a one-hour span.
    """
    if not isinstance(windows, list):
        return []
    out: list[dict[str, int]] = []
    for w in windows:
        if not isinstance(w, dict):
            continue
        bounds = _window_bounds_from_dict(w)
        if bounds is None:
            continue
        s, e = bounds
        s = max(0, min(23, s))
        if e == 24:
            e = 23
        e = max(0, min(23, e))
        if e < s:
            e = s
        if e == s:
            if s < 23:
                e = s + 1
            elif s == 23:
                s, e = 22, 23
            else:
                e = min(23, s + 1)
        out.append({"startHour": s, "endHour": e})
    return out


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
        raw_windows = [{"startHour": start_hour, "endHour": end_hour}]
        span = half_open_span_hours_from_windows(raw_windows)
        normalized[block.appliance_id] = {
            "maxShiftHours": None,
            "allowedWindows": _normalize_allowed_windows_for_optimizer(raw_windows),
            "_halfOpenSpanHours": span,
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

    Body JSON may include optional ``ratePlan`` (int, default 1) to select the utility rate plan
    used for ``rateVector`` (e.g. 1 default, or 2, 6, 7, 9). The response echoes ``ratePlan``.
    """
    body = request.get_json(silent=True) or {}
    uuid = (body.get("uuid") or "").strip()
    user_uuid = (body.get("userUuid") or "").strip() or None
    timezone = (body.get("timezone") or "UTC").strip()

    # Optional: rate plan selector (defaults to 1, must be positive int)
    try:
        rate_plan = int(body.get("ratePlan", 1))
    except Exception:
        rate_plan = 1
    if rate_plan <= 0:
        rate_plan = 1

    if not uuid:
        return {"error": "uuid is required"}, 400

    # Optional constraints input:
    # - constraintText: "Charge EV before 2 AM"
    # - OR constraints: {"constraints":[{appliance_id, load_start_time, load_end_time}, ...]}
    constraint_text = (body.get("constraintText") or body.get("constraint_text") or "").strip()
    constraints_payload = body.get("constraints")

    try:
        merged = build_merged_for_uuid(
            uuid,
            user_uuid=user_uuid,
            out_dir=Path(__file__).resolve().parent / "docs",
            shiftable_ids=set(SHIFTABLE_APPLIANCE_IDS),
            timezone=timezone,
            rate_plan=rate_plan,
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

    # itemizationDetailsList[].id = appId; usage = consumption (dashboard convention).
    current_by_app = current_by_app_from_latest_row(
        latest_row,
        shiftable_ids=SHIFTABLE_APPLIANCE_IDS,
    )
    bill_cost_by_app = bill_cost_by_app_from_latest_row(
        latest_row,
        shiftable_ids=SHIFTABLE_APPLIANCE_IDS,
    )

    # Build constraints map (if any)
    constraints_by_app: dict[int, dict[str, object]] = {}
    if isinstance(constraints_payload, dict) and isinstance(constraints_payload.get("constraints"), list):
        try:
            parsed = AnalyzeConstraintRequest.model_validate(
                {"constraintText": "", "constraints": constraints_payload.get("constraints")}
            )
            constraints_by_app.update(
                _constraints_from_payload(parsed.constraints, shiftable_appliance_ids=SHIFTABLE_APPLIANCE_IDS)
            )
        except Exception as e:  # noqa: BLE001
            return {"error": f"Invalid constraints payload: {e}"}, 400
    if constraint_text:
        text_result = analyze_constraint_text(
            constraint_text,
            shiftable_appliance_ids=SHIFTABLE_APPLIANCE_IDS,
            appliance_catalog=APPLIANCE_CATALOG,
        )
        # Text constraints override payload constraints for same appliance
        constraints_by_app.update(text_result.appliance_constraints)
        constraints_by_app = merge_fallback_where_windows_missing(
            constraint_text,
            constraints_by_app,
            shiftable_appliance_ids=SHIFTABLE_APPLIANCE_IDS,
        )
        constraints_by_app = filter_constraints_to_inferred_appliances(
            constraint_text,
            constraints_by_app,
            shiftable_appliance_ids=SHIFTABLE_APPLIANCE_IDS,
        )

    for _aid, _c in list(constraints_by_app.items()):
        if _c.get("allowedWindows") is not None:
            _c["allowedWindows"] = _normalize_allowed_windows_for_optimizer(_c["allowedWindows"])

    def _constraint_is_applicable(c: dict[str, object]) -> bool:
        if c.get("maxShiftHours") is not None:
            return True
        w = c.get("allowedWindows")
        return isinstance(w, list) and len(w) > 0

    def _constraints_applicable_map(
        cmap: dict[int, dict[str, object]],
    ) -> dict[int, dict[str, object]]:
        return {aid: c for aid, c in cmap.items() if _constraint_is_applicable(c)}

    def _serialize_constraints_by_appliance(
        cmap: dict[int, dict[str, object]],
    ) -> list[dict[str, object]]:
        return [
            {
                "applianceId": int(aid),
                "maxShiftHours": c.get("maxShiftHours"),
                "allowedWindows": c.get("allowedWindows"),
                "halfOpenSpanHours": c.get("_halfOpenSpanHours"),
                "appliedToOptimizer": _constraint_is_applicable(c),
            }
            for aid, c in sorted(cmap.items(), key=lambda x: x[0])
        ]

    user_requested_constraints = bool(constraint_text) or (
        isinstance(constraints_payload, dict)
        and isinstance(constraints_payload.get("constraints"), list)
        and len(constraints_payload.get("constraints") or []) > 0
    )

    applicable_constraints = _constraints_applicable_map(constraints_by_app)

    def _apply_constraints_to_merged(
        base: dict, constraints_map: dict[int, dict[str, object]]
    ) -> dict:
        if not constraints_map:
            return base
        out = deepcopy(base)
        for app in out.get("appliances") or []:
            if not isinstance(app, dict):
                continue
            aid = app.get("appId")
            if not isinstance(aid, int) or aid not in constraints_map:
                continue
            c = constraints_map[aid]
            for blk in app.get("blocks") or []:
                if not isinstance(blk, dict):
                    continue
                existing = blk.get("constraints")
                if not isinstance(existing, dict):
                    existing = {}
                # Preserve existing maxShiftHours unless constraint specifies it.
                if c.get("maxShiftHours") is not None:
                    existing["maxShiftHours"] = c.get("maxShiftHours")
                if c.get("allowedWindows") is not None:
                    nw = _normalize_allowed_windows_for_optimizer(c.get("allowedWindows"))
                    # Never send allowedWindows: [] — many optimizers 400 on empty list.
                    if nw:
                        max_span = int(c.get("_halfOpenSpanHours") or 0)
                        if max_span <= 0:
                            max_span = 24
                        dur = int(blk.get("duration") or 0)
                        if dur <= max_span:
                            existing["allowedWindows"] = nw
                        else:
                            logger.info(
                                "Omitting allowedWindows for appId=%s blockId=%s: "
                                "duration=%sh > halfOpenSpanHours=%sh (infeasible window)",
                                aid,
                                blk.get("blockId"),
                                dur,
                                max_span,
                            )
                blk["constraints"] = existing
        return out

    merged_constrained = None
    if user_requested_constraints:
        if applicable_constraints:
            merged_constrained = _apply_constraints_to_merged(merged, applicable_constraints)
        else:
            merged_constrained = deepcopy(merged)

    def _call_optimizer(payload: dict) -> dict:
        opt_resp = requests.post(
            OPTIMIZER_OPTIMIZE_URL,
            json=payload,
            timeout=300,
        )
        log_optimizer_http_response(logger, opt_resp)
        if opt_resp.status_code >= 400:
            logger.error(
                "optimizer HTTP %s body (truncated): %s",
                opt_resp.status_code,
                (opt_resp.text or "")[:4000],
            )
        opt_resp.raise_for_status()
        data = opt_resp.json()
        return data if isinstance(data, dict) else {"raw": data}

    try:
        # Run baseline + constrained optimizer calls in parallel when both are present
        if merged_constrained:
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
                fut_baseline = executor.submit(_call_optimizer, merged)
                fut_constrained = executor.submit(_call_optimizer, merged_constrained)
                opt_json_baseline = fut_baseline.result()
                opt_json_constrained = fut_constrained.result()
        else:
            opt_json_baseline = _call_optimizer(merged)
            opt_json_constrained = None
    except requests.HTTPError as e:
        logger.exception("build_merged_optimize: optimizer HTTP error")
        detail = ""
        if e.response is not None:
            detail = (e.response.text or "")[:8000]
        return Response(
            json.dumps(
                {
                    "error": str(e),
                    "optimizerStatusCode": getattr(e.response, "status_code", None),
                    "optimizerResponseBody": detail,
                },
                ensure_ascii=False,
            ),
            status=502,
            mimetype="application/json",
        )
    except Exception as e:  # noqa: BLE001
        logger.exception("build_merged_optimize: optimizer call failed")
        return {"error": str(e)}, 502

    def _build_final_for_optimizer(opt_json: dict) -> dict:
        savings_by_app = extract_savings_by_app(opt_json)
        insight_by_app = build_insight_by_app_id(
            opt_json=opt_json,
            current_by_app=current_by_app,
            savings_by_app=savings_by_app,
            shiftable_app_ids=SHIFTABLE_APPLIANCE_IDS,
            appliance_catalog=APPLIANCE_CATALOG,
        )

        appliances_out = []
        total_shiftable_cost_savings = 0.0
        total_shiftable_consumption_savings = 0.0
        for app_id in SHIFTABLE_APPLIANCE_IDS:
            cur = current_by_app.get(app_id, {"cost": 0.0, "consumption": 0.0})
            sav = savings_by_app.get(app_id, {"costSavings": 0.0, "consumptionSavings": 0.0})
            best_cost = max(0.0, float(cur["cost"]) - float(sav["costSavings"] or 0.0))
            best_cons = float(cur["consumption"])
            appliances_out.append(
                {
                    "appId": app_id,
                    "name": APPLIANCE_CATALOG.get(app_id, f"UNKNOWN_{app_id}"),
                    "current": cur,
                    "savings": sav,
                    "best": {"cost": best_cost, "consumption": best_cons},
                    "insight": insight_by_app.get(app_id, ""),
                }
            )
            total_shiftable_cost_savings += float(sav["costSavings"] or 0.0)
            total_shiftable_consumption_savings += float(sav["consumptionSavings"] or 0.0)

        return {
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
        }

    baseline_final = _build_final_for_optimizer(opt_json_baseline)
    constrained_final = (
        _build_final_for_optimizer(opt_json_constrained) if opt_json_constrained else None
    )

    def _format_allowed_windows_label(windows: object) -> str:
        if not isinstance(windows, list) or not windows:
            return ""
        parts: list[str] = []
        for win in windows:
            if not isinstance(win, dict):
                continue
            try:
                sh = int(win["startHour"])
                eh = int(win["endHour"])
                parts.append(f"{sh:02d}:00–{eh:02d}:00")
            except Exception:
                continue
        return ", ".join(parts)

    def _baseline_savings_by_app(baseline: dict) -> dict[int, float]:
        out: dict[int, float] = {}
        for row in baseline.get("appliances") or []:
            if not isinstance(row, dict):
                continue
            aid = row.get("appId")
            if not isinstance(aid, int):
                continue
            sav = row.get("savings") or {}
            if isinstance(sav, dict):
                out[aid] = float(sav.get("costSavings") or 0.0)
        return out

    baseline_savings_by_app = _baseline_savings_by_app(baseline_final)

    def _enrich_constrained_branch(
        cf: dict,
        *,
        constraints_map: dict[int, dict[str, object]],
        applicable_map: dict[int, dict[str, object]],
    ) -> None:
        unresolved = [
            int(aid)
            for aid, c in constraints_map.items()
            if not _constraint_is_applicable(c)
        ]
        msg_parts: list[str] = []
        if user_requested_constraints and not constraints_map:
            msg_parts.append(
                "Constraint input was provided but no per-appliance rules could be parsed."
            )
        if applicable_map:
            msg_parts.append(
                "The constrained POST /optimize run applied allowed windows (or max-shift) "
                "on the merged payload where valid."
            )
        if unresolved:
            msg_parts.append(
                f"No valid allowed window for appliance id(s) {unresolved}; "
                "those appliances were not time-restricted in the constrained run."
            )
        cf.setdefault("total", {})
        cf["total"]["constraintSummary"] = {
            "userProvidedConstraint": user_requested_constraints,
            "constraintsAppliedToOptimizer": bool(applicable_map),
            "appliancesWithUnresolvedConstraints": unresolved,
            "message": " ".join(msg_parts).strip(),
        }
        for row in cf.get("appliances") or []:
            if not isinstance(row, dict):
                continue
            aid = row.get("appId")
            if not isinstance(aid, int):
                continue
            if user_requested_constraints:
                base_sv = float(baseline_savings_by_app.get(aid, 0.0))
                con_sv = float((row.get("savings") or {}).get("costSavings") or 0.0)
                row["vsBaseline"] = {
                    "baselineCostSavings": base_sv,
                    "constrainedCostSavings": con_sv,
                    "deltaConstrainedMinusBaseline": round(con_sv - base_sv, 6),
                }
            if not user_requested_constraints:
                row["constraint"] = {
                    "requested": False,
                    "appliedToOptimizer": False,
                    "allowedWindows": None,
                    "message": "",
                }
                continue
            if aid not in constraints_map:
                row["constraint"] = {
                    "requested": False,
                    "appliedToOptimizer": False,
                    "allowedWindows": None,
                    "message": "",
                }
                continue
            if aid in applicable_map:
                ac = applicable_map[aid]
                wins = ac.get("allowedWindows")
                label = _format_allowed_windows_label(wins)
                base_sav = float(baseline_savings_by_app.get(aid, 0.0))
                con_sav = float((row.get("savings") or {}).get("costSavings") or 0.0)
                cur_cost = float((row.get("current") or {}).get("cost") or 0.0)
                cur_use = float((row.get("current") or {}).get("consumption") or 0.0)
                msg_core = (
                    f"Your constraint was applied on the optimizer input for this appliance "
                    f"(allowed window(s): {label})."
                    if label
                    else "Your constraint was applied on the optimizer input for this appliance (max shift hours)."
                )
                explain: list[str] = []
                if cur_cost <= 0 and cur_use <= 0:
                    explain.append(
                        "Dashboard shows no bill cost or usage for this appliance this cycle, "
                        "so there is nothing meaningful to shift."
                    )
                elif con_sav <= 1e-9 and base_sav <= 1e-9:
                    explain.append(
                        "Best-case and constrained runs both show no cost savings for this appliance "
                        "(usage may already sit in cheaper hours, or blocks cannot improve vs rates)."
                    )
                elif con_sav < base_sav - 1e-6:
                    explain.append(
                        f"Constrained savings ({con_sav:.4f}) are below best-case ({base_sav:.4f}) "
                        "for this appliance—the allowed window limits cheaper move options."
                    )
                elif con_sav <= 1e-9 < base_sav:
                    explain.append(
                        f"Best-case had savings ({base_sav:.4f}) but constrained is 0; the window likely "
                        "blocks the shifts the optimizer used in the unrestricted run."
                    )
                row["constraint"] = {
                    "requested": True,
                    "appliedToOptimizer": True,
                    "allowedWindows": wins,
                    "message": msg_core + (" " + " ".join(explain) if explain else ""),
                }
            else:
                row["constraint"] = {
                    "requested": True,
                    "appliedToOptimizer": False,
                    "allowedWindows": None,
                    "message": (
                        "This appliance was referenced in your constraint, but no usable allowed "
                        "window could be derived, so it was not restricted in the constrained "
                        "optimizer run (same as best-case for this appliance)."
                    ),
                }

    if constrained_final is not None and user_requested_constraints:
        _enrich_constrained_branch(
            constrained_final,
            constraints_map=constraints_by_app,
            applicable_map=applicable_constraints,
        )

    def _insights_without_facts(payload: object) -> object:
        if not isinstance(payload, dict):
            return payload
        return {k: v for k, v in payload.items() if k != "facts"}

    # Build rich insights payloads for both runs.
    insights_service = LoadShiftInsightService()
    baseline_insights = insights_service.generate_insight(
        opt_json_baseline,
        bill_cost_by_app=bill_cost_by_app,
        bill_share_only=True,
    )
    constrained_insights = (
        insights_service.generate_insight(
            opt_json_constrained,
            bill_cost_by_app=bill_cost_by_app,
            bill_share_only=True,
        )
        if opt_json_constrained
        else None
    )

    final = {
        "ratePlan": rate_plan,
        "billCycle": {
            "intervalStart": latest_row.get("intervalStart"),
            "intervalEnd": latest_row.get("intervalEnd"),
        },
        "inputs": {
            "baseline": {"hasConstraints": False},
            "constrained": {
                "hasConstraints": user_requested_constraints,
                "constraintsAppliedToOptimizer": bool(applicable_constraints),
                "constraintText": constraint_text or None,
                "constraintsByAppliance": (
                    _serialize_constraints_by_appliance(constraints_by_app)
                    if constraints_by_app
                    else None
                ),
            },
        },
        "baseline": {
            **baseline_final,
            "insights": _insights_without_facts(baseline_insights),
        },
        "constrained": (
            {
                **(constrained_final or {}),
                "insights": _insights_without_facts(constrained_insights),
            }
            if opt_json_constrained
            else None
        ),
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
                        halfOpenSpanHours=block.get("_halfOpenSpanHours"),
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
