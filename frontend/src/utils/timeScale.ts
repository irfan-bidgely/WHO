/** Timeline maps 0–100% to one full day (midnight → next midnight). */

const HOURS_PER_DAY = 24;

export function pctToMinutes(pct: number): number {
  return (pct / 100) * HOURS_PER_DAY * 60;
}

/** Snap percentage to nearest hour boundary. */
export function snapPctToHour(pct: number): number {
  const hour = (pct / 100) * HOURS_PER_DAY;
  const snapped = Math.round(hour) / HOURS_PER_DAY;
  return Math.min(100, Math.max(0, snapped * 100));
}

/** 12-hour clock label for hour index 0–23. */
export function formatHourLabel(hour: number): string {
  const h = ((hour % 24) + 24) % 24;
  if (h === 0) return '12 AM';
  if (h < 12) return `${h} AM`;
  if (h === 12) return '12 PM';
  return `${h - 12} PM`;
}

/** Compact range string for tooltips / aria. */
export function formatPctRangeLabel(startPct: number, endPct: number): string {
  const start = minutesTo12h(pctToMinutes(startPct));
  const end = minutesTo12h(pctToMinutes(endPct));
  return `${start} – ${end}`;
}

function minutesTo12h(totalMinutes: number): string {
  const m = Math.round(totalMinutes) % (24 * 60);
  const h24 = Math.floor(m / 60);
  const min = m % 60;
  const suffix = h24 >= 12 ? 'PM' : 'AM';
  let h12 = h24 % 12;
  if (h12 === 0) h12 = 12;
  const mm = min.toString().padStart(2, '0');
  return `${h12}:${mm} ${suffix}`;
}
