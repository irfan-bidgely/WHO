#!/usr/bin/env python3
"""
Merge rate vector JSON + appliance blocks JSON into one payload.

Output shape:
{
  "metadata": {...},
  "rates": { "rateVector": [...] },
  "appliances": [...]
}

If total_time_slots is set (>0), the rateVector is padded/truncated to that length.
"""

from __future__ import annotations

import argparse
import json
from copy import deepcopy
from pathlib import Path
from typing import Any, Optional


STATIC_METADATA: dict[str, Any] = {
    "granularity": 3600,
    "startTime": 1771398000,
    "timezone": "Asia/Kolkata",
    "currency": "INR",
}


def load_rate_vector(path: Path) -> list[float]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if "ratesPerKwh" in data:
        return [float(x) for x in data["ratesPerKwh"]]
    if "rateVector" in data:
        return [float(x) for x in data["rateVector"]]
    raise ValueError(f"No ratesPerKwh or rateVector in {path}")


def load_appliances(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    apps = data.get("appliances")
    if not isinstance(apps, list):
        raise ValueError(f"No appliances[] in {path}")
    return apps


def attach_constraints(appliances: list[dict], max_shift_hours: int) -> list[dict]:
    out: list[dict] = []
    for app in appliances:
        app = deepcopy(app)
        if app.get("shiftable"):
            for b in app.get("blocks") or []:
                b["constraints"] = {"maxShiftHours": max_shift_hours}
        out.append(app)
    return out


def fit_rate_vector(vec: list[float], target_slots: Optional[int]) -> list[float]:
    if target_slots is None or target_slots <= 0:
        return vec
    if len(vec) == target_slots:
        return vec
    if len(vec) > target_slots:
        return vec[:target_slots]
    if not vec:
        return [0.0] * target_slots
    last = vec[-1]
    return vec + [last] * (target_slots - len(vec))


def merge(
    *,
    rates_path: Path,
    appliances_path: Path,
    max_shift_hours: int = 10,
    start_time_override: Optional[int] = None,
    total_time_slots: Optional[int] = 0,
) -> dict:
    rate_vector = fit_rate_vector(load_rate_vector(rates_path), total_time_slots if total_time_slots else None)
    appliances = attach_constraints(load_appliances(appliances_path), max_shift_hours)

    metadata = {**STATIC_METADATA}
    if start_time_override is not None:
        metadata["startTime"] = start_time_override
    metadata["totalTimeSlots"] = len(rate_vector)

    return {
        "metadata": metadata,
        "rates": {"rateVector": rate_vector},
        "appliances": appliances,
    }


def main() -> None:
    here = Path(__file__).resolve().parent
    backend = here.parent

    p = argparse.ArgumentParser(description="Merge rate vector + appliance blocks JSON.")
    p.add_argument("--rates", type=Path, required=True)
    p.add_argument("--appliances", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--max-shift-hours", type=int, default=10)
    p.add_argument("--start-time", type=int, default=None)
    p.add_argument("--total-time-slots", type=int, default=0)
    args = p.parse_args()

    doc = merge(
        rates_path=args.rates,
        appliances_path=args.appliances,
        max_shift_hours=args.max_shift_hours,
        start_time_override=args.start_time,
        total_time_slots=args.total_time_slots,
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote {args.out} ({doc['metadata']['totalTimeSlots']} slots, {len(doc['appliances'])} appliances)")


if __name__ == "__main__":
    main()

