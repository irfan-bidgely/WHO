#!/usr/bin/env python3
"""
Build appliance block JSON from tbdata_by_app.json + appliance_mapping.json.

- Linear time index start_t = (UTC calendar day index from bill start) * 24 + hour (0–23).
  Bill anchor: each app's top-level ``start`` timestamp (fallback: min interval start).
- Merge consecutive hourly intervals into one block when end == next start AND both
  interval *start* times fall on the same UTC calendar day (no merge across midnight).
- shiftable: true if appId is in SHIFTABLE_IDS (default 18,2,3,4,7).

Usage (from repo root or backend):
  python backend/scripts/build_appliance_blocks.py
  python backend/scripts/build_appliance_blocks.py --out backend/docs/appliance_blocks.json
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


DEFAULT_SHIFTABLE = [18, 2, 3, 4, 7]


def load_mapping(path: Path) -> dict[str, str]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return {str(k): str(v) for k, v in raw.items()}


def appliance_name(mapping: dict[str, str], app_id: int) -> str:
    return mapping.get(str(app_id), f"UNKNOWN_{app_id}")


def interval_start_date_utc(ts: int) -> datetime:
    return datetime.fromtimestamp(ts, tz=timezone.utc).date()


def interval_start_hour_utc(ts: int) -> int:
    return datetime.fromtimestamp(ts, tz=timezone.utc).hour


def linear_start_t(bill_anchor_ts: int, interval_start_ts: int) -> int:
    bill_date = interval_start_date_utc(bill_anchor_ts)
    t_date = interval_start_date_utc(interval_start_ts)
    day_index = (t_date - bill_date).days
    hour = interval_start_hour_utc(interval_start_ts)
    return day_index * 24 + hour


def merge_blocks(
    intervals: list[dict],
    bill_anchor_ts: int,
    *,
    value_scale: float = 1.0,
) -> list[dict]:
    """Return list of {start_t, duration, consumption: [...]}."""
    if not intervals:
        return []

    sorted_iv = sorted(intervals, key=lambda x: int(x["start"]))
    blocks: list[dict] = []
    cur_values: list[float] = []
    cur_start_t: Optional[int] = None

    def flush() -> None:
        nonlocal cur_values, cur_start_t
        if cur_start_t is None:
            return
        blocks.append(
            {
                "start_t": cur_start_t,
                "duration": len(cur_values),
                "consumption": [round(v, 6) for v in cur_values],
            }
        )
        cur_values = []
        cur_start_t = None

    for inv in sorted_iv:
        s = int(inv["start"])
        e = int(inv["end"])
        v = float(inv["value"]) * float(value_scale)

        st = linear_start_t(bill_anchor_ts, s)
        s_date = interval_start_date_utc(s)

        if cur_start_t is None:
            cur_start_t = st
            cur_values.append(v)
            prev_end = e
            prev_start_date = s_date
            continue

        contiguous = s == prev_end
        same_day = s_date == prev_start_date

        if contiguous and same_day:
            cur_values.append(v)
            prev_end = e
            prev_start_date = s_date
        else:
            flush()
            cur_start_t = st
            cur_values.append(v)
            prev_end = e
            prev_start_date = s_date

    flush()
    return blocks


def build_document(
    tb_path: Path,
    mapping_path: Path,
    shiftable_ids: set[int],
    *,
    value_scale: float = 1.0,
) -> dict:
    tb = json.loads(tb_path.read_text(encoding="utf-8"))
    mapping = load_mapping(mapping_path)

    appliances_out: list[dict] = []

    for key in sorted(tb.keys(), key=lambda x: int(x)):
        app = tb[key]
        app_id = int(app.get("appId", key))
        intervals = app.get("intervals") or []
        bill_anchor = int(app.get("start") or min(int(i["start"]) for i in intervals))

        blocks_raw = merge_blocks(intervals, bill_anchor, value_scale=value_scale)
        blocks = []
        for i, b in enumerate(blocks_raw, start=1):
            blocks.append(
                {
                    "blockId": i,
                    "start_t": b["start_t"],
                    "duration": b["duration"],
                    "consumption": b["consumption"],
                }
            )

        appliances_out.append(
            {
                "appId": app_id,
                "name": appliance_name(mapping, app_id),
                "shiftable": app_id in shiftable_ids,
                "blocks": blocks,
            }
        )

    return {"appliances": appliances_out}


def main() -> None:
    here = Path(__file__).resolve()
    backend = here.parent.parent
    repo = backend.parent

    p = argparse.ArgumentParser(description="Build appliance blocks JSON from tbdata_by_app.")
    p.add_argument(
        "--tbdata",
        type=Path,
        default=backend / "docs" / "tbdata_by_app.json",
        help="Path to tbdata_by_app.json",
    )
    p.add_argument(
        "--mapping",
        type=Path,
        default=backend / "appliance_mapping.json",
        help="Path to appliance_mapping.json",
    )
    p.add_argument(
        "--out",
        type=Path,
        default=backend / "docs" / "appliance_blocks.json",
        help="Output JSON path",
    )
    p.add_argument(
        "--shiftable",
        type=str,
        default=",".join(str(x) for x in DEFAULT_SHIFTABLE),
        help="Comma-separated appIds that are shiftable (default: 18,2,3,4,7)",
    )
    p.add_argument(
        "--value-scale",
        type=float,
        default=1.0,
        help="Multiply each tbValues entry by this scale (e.g. 0.001 to convert Wh->kWh).",
    )
    args = p.parse_args()

    shiftable_ids = {int(x.strip()) for x in args.shiftable.split(",") if x.strip()}

    doc = build_document(args.tbdata, args.mapping, shiftable_ids, value_scale=args.value_scale)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    n_apps = len(doc["appliances"])
    n_blocks = sum(len(a["blocks"]) for a in doc["appliances"])
    print(f"Wrote {args.out} ({n_apps} appliances, {n_blocks} blocks total)")


if __name__ == "__main__":
    main()
