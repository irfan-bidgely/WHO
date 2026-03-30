#!/usr/bin/env python3
"""
Read docs/tbdata.json (hour_aggregated_data: list of JSON strings per app),
zip tbStartList / tbEndList / tbValues by index, write one JSON object per appId.

Usage:
  python scripts/transform_tbdata.py
  python scripts/transform_tbdata.py -i path/in.json -o path/out.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def parse_hour_record(raw: str | dict) -> dict:
    if isinstance(raw, str):
        return json.loads(raw)
    return raw


def intervals_from_record(obj: dict) -> list[dict]:
    starts = obj.get("tbStartList") or []
    ends = obj.get("tbEndList") or []
    values = obj.get("tbValues") or []
    n = min(len(starts), len(ends), len(values))
    if not (len(starts) == len(ends) == len(values)):
        # still emit aligned prefix; caller could log if needed
        pass
    return [
        {"start": starts[i], "end": ends[i], "value": values[i]}
        for i in range(n)
    ]


def transform_tbdata(root: dict) -> dict[str, dict]:
    """
    Returns mapping app_id_str -> {
      appId, start, end, granularity, intervals: [{start, end, value}, ...]
    }
    """
    out: dict[str, dict] = {}
    for raw in root.get("hour_aggregated_data", []):
        obj = parse_hour_record(raw)
        app_id = obj["appId"]
        key = str(app_id)
        out[key] = {
            "appId": app_id,
            "start": obj.get("start"),
            "end": obj.get("end"),
            "granularity": obj.get("granularity"),
            "intervals": intervals_from_record(obj),
        }
    return out


def main() -> None:
    repo = Path(__file__).resolve().parents[1]
    default_in = repo / "docs" / "tbdata.json"
    default_out = repo / "docs" / "tbdata_by_app.json"

    p = argparse.ArgumentParser(description="Zip tbStartList/tbEndList/tbValues by index per appId.")
    p.add_argument("-i", "--input", type=Path, default=default_in, help="Input tbdata.json")
    p.add_argument("-o", "--output", type=Path, default=default_out, help="Output JSON path")
    p.add_argument("--indent", type=int, default=2, help="JSON indent (0 = compact)")
    args = p.parse_args()

    data = json.loads(args.input.read_text(encoding="utf-8"))
    transformed = transform_tbdata(data)

    indent = None if args.indent == 0 else args.indent
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(transformed, indent=indent, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"Wrote {len(transformed)} app(s) -> {args.output}")


if __name__ == "__main__":
    main()
