/** Matches backend ``RATE_PLAN_DISPLAY_NAMES`` (fallback if API omits ``ratePlanName``). */
const RATE_PLAN_LABELS: Record<number, string> = {
  1: 'RTOUE47',
  9: 'ET-1',
  6: 'R-2',
  7: 'ET-2',
};

export function ratePlanDisplayName(planId: number): string {
  const label = RATE_PLAN_LABELS[planId];
  if (label) return label;
  return `Plan ${planId}`;
}
