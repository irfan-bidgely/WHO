"""
Build an hourly rate vector for a billing window from utility rate configuration.

Fetches ``/v3.0/rates/configuration/utilityId/{utility_id}``, selects a plan by name,
then assigns one **CONSUMPTION_BASED** marginal rate per clock hour in
``[bill_start_epoch, bill_end_epoch)`` (fixed and demand charges are ignored).
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timedelta, timezone as dt_timezone
from typing import Any, Sequence
from zoneinfo import ZoneInfo

import requests

import api_settings

logger = logging.getLogger(__name__)

CHARGE_CONSUMPTION = "CONSUMPTION_BASED"

# API uses this pair as "no validity restriction" for rate rows
_VALID_SENTINEL_PREFIX = "1970-01-01"


def _epoch_to_seconds(value: Any) -> int | None:
    if value is None:
        return None
    try:
        n = int(float(value))
    except (TypeError, ValueError):
        return None
    if n > 10_000_000_000:  # assume milliseconds
        n //= 1000
    return n


def _is_open_valid_window(valid_low: Any, valid_high: Any) -> bool:
    """True when API omits real validity bounds (nulls or 1970-01-01 placeholders)."""
    if valid_low is None and valid_high is None:
        return True
    lo = valid_low if isinstance(valid_low, str) else None
    hi = valid_high if isinstance(valid_high, str) else None
    if lo is not None and hi is not None:
        if lo.strip().startswith(_VALID_SENTINEL_PREFIX) and hi.strip().startswith(
            _VALID_SENTINEL_PREFIX
        ):
            return True
    return False


def _date_string_start_utc(s: str) -> int:
    """Start of calendar day ``YYYY-MM-DD`` in UTC."""
    part = s.strip()[:10]
    dt = datetime.strptime(part, "%Y-%m-%d").replace(tzinfo=dt_timezone.utc)
    return int(dt.timestamp())


def _date_string_end_inclusive_utc(s: str) -> int:
    """Last second of calendar day ``YYYY-MM-DD`` in UTC."""
    part = s.strip()[:10]
    start = datetime.strptime(part, "%Y-%m-%d").replace(tzinfo=dt_timezone.utc)
    nxt = start + timedelta(days=1)
    return int(nxt.timestamp()) - 1


def _valid_inclusive_epoch_bounds(rate: dict[str, Any]) -> tuple[int | None, int | None]:
    """
    Return (low, high) epoch seconds inclusive, or (None, None) if unrestricted.
    Supports numeric epochs and ``YYYY-MM-DD`` strings.
    """
    lo_raw = rate.get("validLow")
    hi_raw = rate.get("validHigh")
    if _is_open_valid_window(lo_raw, hi_raw):
        return None, None

    lo: int | None = None
    hi: int | None = None

    if isinstance(lo_raw, str) and len(lo_raw.strip()) >= 10:
        try:
            lo = _date_string_start_utc(lo_raw)
        except ValueError:
            lo = _epoch_to_seconds(lo_raw)
    else:
        lo = _epoch_to_seconds(lo_raw)

    if isinstance(hi_raw, str) and len(hi_raw.strip()) >= 10:
        try:
            hi = _date_string_end_inclusive_utc(hi_raw)
        except ValueError:
            hi = _epoch_to_seconds(hi_raw)
    else:
        hi = _epoch_to_seconds(hi_raw)

    return lo, hi


def _normalize_api_host(api_host: str) -> str:
    return api_host.rstrip("/")


def fetch_rate_configuration(
    api_host: str,
    utility_id: int,
    access_token: str,
    timeout_s: float = 60.0,
) -> list[dict[str, Any]]:
    """GET rate configuration list for a utility."""
    base = _normalize_api_host(api_host)
    url = f"{base}/v3.0/rates/configuration/utilityId/{utility_id}"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
    }
    logger.info("Fetching rate configuration", extra={"utility_id": utility_id})
    resp = requests.get(url, headers=headers, timeout=timeout_s)
    resp.raise_for_status()
    data = resp.json()
    if isinstance(data, dict):
        err = data.get("error")
        if err is not None and err != "":
            raise ValueError(f"Rate configuration API error: {err!r}")
        payload = data.get("payload")
        if not isinstance(payload, list):
            raise ValueError("Rate configuration response must include a list payload")
        return payload
    if isinstance(data, list):
        return data
    raise ValueError("Rate configuration response must be a JSON object with payload or a list")


def select_plan_by_name(
    plans: Sequence[dict[str, Any]],
    rate_plan: str,
) -> dict[str, Any]:
    """Pick plan where ``planName`` matches (or string form of ``planNumber``)."""
    target = str(rate_plan).strip()
    for p in plans:
        name = p.get("planName")
        if name is not None and str(name).strip() == target:
            return p
        num = p.get("planNumber")
        if num is not None and str(num).strip() == target:
            return p
    names = [p.get("planName") for p in plans]
    raise ValueError(f"No rate plan matching {rate_plan!r}. Available planName values: {names}")


def _matches_calendar(rate: dict[str, Any], dt: datetime) -> bool:
    m_low = int(rate.get("monthLow", 1))
    m_high = int(rate.get("monthHigh", 12))
    d_low = int(rate.get("dayLow", 1))
    d_high = int(rate.get("dayHigh", 31))
    if not (m_low <= dt.month <= m_high):
        return False
    if not (d_low <= dt.day <= d_high):
        return False
    w_low = int(rate.get("weekLow", 1))
    w_high = int(rate.get("weekHigh", 7))
    if not (w_low <= dt.isoweekday() <= w_high):
        return False
    h_low = int(rate.get("timeOfDayLow", 0))
    h_high = int(rate.get("timeOfDayHigh", 23))
    if not (h_low <= dt.hour <= h_high):
        return False
    return True


def _matches_valid_window(rate: dict[str, Any], hour_start_epoch: int) -> bool:
    lo, hi = _valid_inclusive_epoch_bounds(rate)
    if lo is not None and hour_start_epoch < lo:
        return False
    if hi is not None and hour_start_epoch > hi:
        return False
    return True


def _consumption_tier_matches(rate: dict[str, Any], cumulative_kwh: float) -> bool:
    lo = int(rate.get("consumptionLow", 0))
    hi = int(rate.get("consumptionHigh", 2_147_483_647))
    return lo <= cumulative_kwh <= hi


def _pick_consumption_rate(
    candidates: list[dict[str, Any]],
    cumulative_kwh: float,
) -> float:
    """Among TOU-matching CONSUMPTION rows, pick tier from cumulative kWh."""
    matching = [r for r in candidates if _consumption_tier_matches(r, cumulative_kwh)]
    if matching:
        best = max(matching, key=lambda r: int(r.get("consumptionLow", 0)))
        return float(best["rate"])
    # fall back: lowest tier that starts at 0
    zeros = [r for r in candidates if int(r.get("consumptionLow", 0)) == 0]
    if zeros:
        return float(min(zeros, key=lambda r: int(r.get("consumptionHigh", 10**9)))["rate"])
    if candidates:
        return float(candidates[0]["rate"])
    return 0.0


def _iter_hour_starts(
    bill_start_epoch: int,
    bill_end_epoch: int,
    tz: ZoneInfo,
) -> list[datetime]:
    if bill_end_epoch <= bill_start_epoch:
        raise ValueError("bill_end_epoch must be greater than bill_start_epoch")
    start = datetime.fromtimestamp(bill_start_epoch, tz=tz)
    end = datetime.fromtimestamp(bill_end_epoch, tz=tz)
    cur = start.replace(minute=0, second=0, microsecond=0)
    if start > cur:
        cur += timedelta(hours=1)
    out: list[datetime] = []
    while cur < end:
        out.append(cur)
        cur += timedelta(hours=1)
    return out


def create_rate_vector(
    rate_plan: str,
    bill_start_epoch: int,
    bill_end_epoch: int,
    *,
    api_host: str | None = None,
    utility_id: int | None = None,
    timezone: str = "UTC",
    hourly_kwh: Sequence[float] | None = None,
    access_token: str | None = None,
) -> list[float]:
    """
    Fetch utility rates, select ``rate_plan`` (``planName`` or ``planNumber`` string),
    and return one **CONSUMPTION_BASED** marginal rate per clock hour.

    ``MONTHLY_FIXED``, ``MONTHLY_DEMAND_BASED``, and any other charge types are
    omitted from the vector.

    Defaults for ``api_host``, ``utility_id``, and token come from
    :mod:`api_settings` unless you pass overrides here.

    Hour timestamps use ``timezone`` (IANA name, e.g. ``America/Los_Angeles``).

    Tier for each hour uses cumulative kWh **before** that hour when ``hourly_kwh``
    is provided (same length as hour count). Otherwise cumulative kWh is treated as
    ``0`` (lowest applicable tier for the time window).

    Rows must match calendar fields (month/day/week/time-of-use) and optional
    ``validLow`` / ``validHigh`` (seconds or milliseconds epoch).
    """
    host = (api_host if api_host is not None else api_settings.API_BASE_URL).strip()
    uid = utility_id if utility_id is not None else api_settings.UTILITY_ID
    if not host:
        raise ValueError("Set API_BASE_URL in backend/api_settings.py or pass api_host=")
    if not isinstance(uid, int) or uid <= 0:
        raise ValueError("Set a positive UTILITY_ID in backend/api_settings.py or pass utility_id=")

    token = (access_token or "").strip() or (api_settings.ACCESS_TOKEN or "").strip()
    if not token:
        logger.error("ACCESS_TOKEN is empty in api_settings.py")
        raise RuntimeError(
            "Set ACCESS_TOKEN in backend/api_settings.py or pass access_token= to create_rate_vector"
        )
    plans = fetch_rate_configuration(host, uid, token)
    return create_rate_vector_from_cached_plans(
        rate_plan,
        bill_start_epoch,
        bill_end_epoch,
        plans,
        timezone=timezone,
        hourly_kwh=hourly_kwh,
    )


def create_rate_vector_from_cached_plans(
    rate_plan: str,
    bill_start_epoch: int,
    bill_end_epoch: int,
    plans: Sequence[dict[str, Any]],
    *,
    timezone: str = "UTC",
    hourly_kwh: Sequence[float] | None = None,
) -> list[float]:
    """Same as :func:`create_rate_vector` but uses an already-fetched plan list (tests/offline)."""
    plan = select_plan_by_name(plans, rate_plan)
    try:
        tz = ZoneInfo(timezone)
    except Exception as e:  # noqa: BLE001
        raise ValueError(f"Invalid timezone: {timezone!r}") from e

    hours = _iter_hour_starts(bill_start_epoch, bill_end_epoch, tz)
    n_hours = len(hours)
    if n_hours == 0:
        return []

    cumulative = 0.0
    vector: list[float] = []

    for i, dt in enumerate(hours):
        hour_epoch = int(dt.timestamp())
        if hourly_kwh is not None:
            if len(hourly_kwh) != n_hours:
                raise ValueError(
                    f"hourly_kwh length {len(hourly_kwh)} must equal hour count {n_hours}"
                )
            cumulative_kwh = cumulative
        else:
            cumulative_kwh = 0.0

        hour_total = 0.0
        for comp in plan.get("components") or []:
            rates = comp.get("rates") or []
            consumption_candidates = [
                r
                for r in rates
                if r.get("chargeType") == CHARGE_CONSUMPTION
                and _matches_calendar(r, dt)
                and _matches_valid_window(r, hour_epoch)
            ]
            if consumption_candidates:
                hour_total += _pick_consumption_rate(consumption_candidates, cumulative_kwh)

        vector.append(hour_total)
        if hourly_kwh is not None:
            cumulative += float(hourly_kwh[i])

    return vector


def _month_bounds_epoch(year: int, month: int, tz: ZoneInfo) -> tuple[int, int]:
    first = datetime(year, month, 1, 0, 0, 0, tzinfo=tz)
    if month == 12:
        nxt = datetime(year + 1, 1, 1, 0, 0, 0, tzinfo=tz)
    else:
        nxt = datetime(year, month + 1, 1, 0, 0, 0, tzinfo=tz)
    return int(first.timestamp()), int(nxt.timestamp())


def main(argv: Sequence[str] | None = None) -> int:
    """CLI: fetch rates and print hourly CONSUMPTION_BASED vector for a billing window."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    p = argparse.ArgumentParser(description="Build hourly consumption rate vector from API.")
    p.add_argument("--plan", default="011ID", help="planName or planNumber (default: 011ID)")
    p.add_argument(
        "--timezone",
        default="UTC",
        help="IANA timezone for hour boundaries (default: UTC)",
    )
    p.add_argument(
        "--month",
        metavar="YYYY-MM",
        help="Bill calendar month in --timezone (default: current month)",
    )
    p.add_argument(
        "--start",
        type=int,
        metavar="EPOCH",
        help="Bill start epoch (seconds); use with --end instead of --month",
    )
    p.add_argument(
        "--end",
        type=int,
        metavar="EPOCH",
        help="Bill end epoch (seconds), exclusive",
    )
    p.add_argument(
        "--json",
        action="store_true",
        help="Print only a JSON array of rates to stdout",
    )
    args = p.parse_args(list(argv) if argv is not None else None)

    try:
        tz = ZoneInfo(args.timezone)
    except Exception as e:  # noqa: BLE001
        logger.error("Invalid timezone %s", args.timezone)
        print(f"Invalid timezone: {args.timezone!r}", file=sys.stderr)
        return 2

    if args.start is not None or args.end is not None:
        if args.start is None or args.end is None:
            print("Both --start and --end are required together.", file=sys.stderr)
            return 2
        start_e, end_e = args.start, args.end
    elif args.month:
        try:
            y_str, m_str = args.month.split("-", 1)
            y, m = int(y_str), int(m_str)
            if not (1 <= m <= 12):
                raise ValueError
        except ValueError:
            print("Use --month YYYY-MM", file=sys.stderr)
            return 2
        start_e, end_e = _month_bounds_epoch(y, m, tz)
    else:
        now = datetime.now(tz)
        start_e, end_e = _month_bounds_epoch(now.year, now.month, tz)

    try:
        vec = create_rate_vector(
            args.plan,
            start_e,
            end_e,
            timezone=args.timezone,
        )
    except Exception as e:  # noqa: BLE001
        logger.exception("create_rate_vector failed")
        print(f"Error: {e}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(vec))
        return 0

    print(f"plan={args.plan!r} timezone={args.timezone!r}")
    print(f"epoch range: {start_e} .. {end_e} (end exclusive)")
    print(f"hours: {len(vec)}")
    if vec:
        head = [round(x, 6) for x in vec[:12]]
        tail = [round(x, 6) for x in vec[-12:]]
        print(f"first 12: {head}")
        print(f"last 12: {tail}")
        print(f"min / max: {min(vec):.6f} / {max(vec):.6f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
