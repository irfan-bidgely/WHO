from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import boto3
import requests

import api_settings
from rate_vector import create_rate_vector
from scripts.build_appliance_blocks import build_document as build_appliance_blocks
from scripts.merge_rates_appliances import merge as merge_rates_appliances
from scripts.transform_tbdata import transform_tbdata


WAREHOUSE_BUCKET = "bidgely-data-warehouse-prod-na"
WAREHOUSE_PREFIX = (
    "tou-appliance-aggregation/hour-aggregated-appliance-data/v1/datasets/"
    "pilot_id=10121"
)


@dataclass(frozen=True)
class DashboardUsageConfig:
    api_base_url: str = "https://naapi-read.bidgely.com"
    access_token: str = ""


def dashboard_usage_config() -> DashboardUsageConfig:
    token = (api_settings.ACCESS_TOKEN or "").strip()
    if not token:
        raise RuntimeError("Missing ACCESS_TOKEN in backend/api_settings.py")
    return DashboardUsageConfig(access_token=token)


def fetch_latest_bill_cycle_from_usage_chart(user_uuid: str, cfg: DashboardUsageConfig) -> tuple[int, int | None]:
    """
    Calls usage-chart-details and returns:
      - bc_start = max(intervalStart) where itemizationDetailsList != null
      - bc_end_exclusive = intervalEnd + 1 when intervalEnd present (intervalEnd is inclusive)
    """
    url = f"{cfg.api_base_url.rstrip('/')}/v2.0/dashboard/users/{user_uuid}/usage-chart-details"
    params = {
        "measurement-type": "ELECTRIC",
        "mode": "year",
        "start": 0,
        "end": 1774946470,
        "date-format": "DATE_TIME",
        "locale": "en_US",
        "next-bill-cycle": "false",
        "show-at-granularity": "false",
        "skip-ongoing-cycle": "false",
    }
    headers = {
        "Authorization": f"Bearer {cfg.access_token}",
        "Accept": "application/json",
        "X-Bidgely-Client-Type": "WIDGETS",
        "X-Bidgely-Pilot-Id": str(api_settings.UTILITY_ID),
    }
    resp = requests.get(url, headers=headers, params=params, timeout=60)
    if resp.status_code >= 400:
        raise RuntimeError(
            f"Dashboard API failed: HTTP {resp.status_code} url={resp.url} body={resp.text[:2000]!r}"
        )
    data = resp.json()
    payload = data.get("payload") or {}
    rows = payload.get("usageChartDataList") or []
    candidates: list[tuple[int, int | None]] = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        if r.get("itemizationDetailsList") is None:
            continue
        if r.get("intervalStart") is None:
            continue
        try:
            s = int(float(r["intervalStart"]))
        except Exception:
            continue
        e = None
        if r.get("intervalEnd") is not None:
            try:
                e = int(float(r["intervalEnd"])) + 1
            except Exception:
                e = None
        candidates.append((s, e))
    if not candidates:
        raise RuntimeError("No intervalStart with itemizationDetailsList != null in dashboard response")
    return max(candidates, key=lambda t: t[0])


def fetch_latest_bill_cycle_row_from_usage_chart(user_uuid: str, cfg: DashboardUsageConfig) -> dict[str, Any]:
    """
    Like fetch_latest_bill_cycle_from_usage_chart, but returns the full row dict
    (the one with max intervalStart where itemizationDetailsList != null).
    """
    url = f"{cfg.api_base_url.rstrip('/')}/v2.0/dashboard/users/{user_uuid}/usage-chart-details"
    params = {
        "measurement-type": "ELECTRIC",
        "mode": "year",
        "start": 0,
        "end": 1774946470,
        "date-format": "DATE_TIME",
        "locale": "en_US",
        "next-bill-cycle": "false",
        "show-at-granularity": "false",
        "skip-ongoing-cycle": "false",
    }
    headers = {
        "Authorization": f"Bearer {cfg.access_token}",
        "Accept": "application/json",
        "X-Bidgely-Client-Type": "WIDGETS",
        "X-Bidgely-Pilot-Id": str(api_settings.UTILITY_ID),
    }
    resp = requests.get(url, headers=headers, params=params, timeout=60)
    if resp.status_code >= 400:
        raise RuntimeError(
            f"Dashboard API failed: HTTP {resp.status_code} url={resp.url} body={resp.text[:2000]!r}"
        )
    data = resp.json()
    payload = data.get("payload") or {}
    rows = payload.get("usageChartDataList") or []
    candidates: list[dict[str, Any]] = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        if r.get("itemizationDetailsList") is None:
            continue
        if r.get("intervalStart") is None:
            continue
        candidates.append(r)
    if not candidates:
        raise RuntimeError("No bill-cycle rows with itemizationDetailsList != null in dashboard response")
    return max(candidates, key=lambda rr: int(float(rr.get("intervalStart") or 0)))


def _s3_client():
    return boto3.client("s3")


def find_latest_dataset_key(uuid: str, bc_start: int) -> str:
    """
    Under:
      .../uuid=<uuid>/bc_start=<bc_start>/
    pick the largest last_updated_timestamp folder and return the (single) json object key inside it.
    """
    s3 = _s3_client()
    base_prefix = f"{WAREHOUSE_PREFIX}/uuid={uuid}/bc_start={bc_start}/"
    paginator = s3.get_paginator("list_objects_v2")

    last_updated_values: list[int] = []
    for page in paginator.paginate(Bucket=WAREHOUSE_BUCKET, Prefix=base_prefix, Delimiter="/"):
        for cp in page.get("CommonPrefixes", []) or []:
            p = (cp.get("Prefix") or "").rstrip("/")
            if "last_updated_timestamp=" not in p:
                continue
            try:
                last_updated_values.append(int(p.split("last_updated_timestamp=")[-1]))
            except Exception:
                continue
    if not last_updated_values:
        raise RuntimeError(f"No last_updated_timestamp prefixes under {base_prefix}")

    latest_ts = max(last_updated_values)
    latest_prefix = f"{base_prefix}last_updated_timestamp={latest_ts}/"

    keys: list[str] = []
    for page in paginator.paginate(Bucket=WAREHOUSE_BUCKET, Prefix=latest_prefix):
        for obj in page.get("Contents", []) or []:
            key = obj.get("Key") or ""
            if key.endswith(".json"):
                keys.append(key)
    if not keys:
        raise RuntimeError(f"No JSON objects under {latest_prefix}")
    if len(keys) == 1:
        return keys[0]
    return sorted(keys)[-1]


def download_s3_json(key: str) -> dict[str, Any]:
    s3 = _s3_client()
    obj = s3.get_object(Bucket=WAREHOUSE_BUCKET, Key=key)
    raw = obj["Body"].read()
    return json.loads(raw.decode("utf-8"))


def _max_block_end(blocks_doc: dict[str, Any]) -> int:
    m = 0
    for app in blocks_doc.get("appliances", []) or []:
        for b in app.get("blocks", []) or []:
            try:
                m = max(m, int(b["start_t"]) + int(b["duration"]))
            except Exception:
                continue
    return m


def build_merged_for_uuid(
    uuid: str,
    *,
    user_uuid: str | None = None,
    out_dir: Path,
    shiftable_ids: set[int],
    timezone: str = "UTC",
    rate_plan: int = 1,
) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)

    cfg = dashboard_usage_config()
    dashboard_user_uuid = user_uuid or uuid
    bc_start, bc_end = fetch_latest_bill_cycle_from_usage_chart(dashboard_user_uuid, cfg)

    # Fetch appliance tbdata from S3
    s3_key = find_latest_dataset_key(uuid, bc_start)
    tbdata = download_s3_json(s3_key)
    (out_dir / "tbdata.json").write_text(json.dumps(tbdata, indent=2) + "\n", encoding="utf-8")
    (out_dir / "tbdata_s3_source.json").write_text(
        json.dumps(
            {
                "bucket": WAREHOUSE_BUCKET,
                "key": s3_key,
                "uuid": uuid,
                "userUuid": dashboard_user_uuid,
                "bc_start": bc_start,
                "bc_end_exclusive": bc_end,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    tb_by_app = transform_tbdata(tbdata)
    tb_by_app_path = out_dir / "tbdata_by_app.json"
    tb_by_app_path.write_text(json.dumps(tb_by_app, indent=2) + "\n", encoding="utf-8")

    # Blocks
    mapping_path = out_dir.parent / "appliance_mapping.json"
    # S3 tbValues are typically in Wh. Optimizer + rates are per-kWh, so emit kWh.
    blocks_doc = build_appliance_blocks(tb_by_app_path, mapping_path, shiftable_ids, value_scale=0.001)
    blocks_path = out_dir / "appliance_blocks.json"
    blocks_path.write_text(json.dumps(blocks_doc, indent=2) + "\n", encoding="utf-8")

    # Rate vector (selected plan)
    if bc_end is None or bc_end <= bc_start:
        ends = [int(v.get("end") or 0) for v in tb_by_app.values()]
        bc_end = max(ends) if ends else bc_start
    rate_vec = create_rate_vector(rate_plan, bc_start, bc_end, timezone=timezone)
    rates_path = out_dir / f"rate_vector_plan{rate_plan}_{bc_start}_{bc_end}.json"
    rates_path.write_text(json.dumps({"rateVector": rate_vec}, indent=2) + "\n", encoding="utf-8")

    # Merge (pad rateVector so totalTimeSlots covers all blocks)
    slot_target = _max_block_end(blocks_doc)
    merged = merge_rates_appliances(
        rates_path=rates_path,
        appliances_path=blocks_path,
        start_time_override=bc_start,
        total_time_slots=slot_target if slot_target > 0 else 0,
    )
    (out_dir / "merged_rates_appliances.json").write_text(
        json.dumps(merged, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return merged

