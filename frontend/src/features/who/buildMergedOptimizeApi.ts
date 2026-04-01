const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://127.0.0.1:5000';

/** Stripped load-shift insights object attached to each optimizer branch (`facts` removed server-side). */
export type LoadShiftInsightsPayload = {
  appliances?: Array<{
    appId?: number;
    name?: string;
    insight?: string;
  }>;
};

/** Per-branch payload from `/api/build-merged-optimize` (baseline vs constrained optimizer run). */
export type OptimizeBranchBody = {
  total: {
    current: { cost: number; consumption: number };
    shiftableSavings: { costSavings: number; consumptionSavings: number };
    best: { cost: number; consumption: number };
  };
  appliances: Array<{
    appId: number;
    name: string;
    insight?: string;
    current: { cost: number; consumption: number };
    savings: { costSavings: number; consumptionSavings: number };
    best: { cost: number; consumption: number };
  }>;
  insights?: LoadShiftInsightsPayload | unknown;
};

export type BuildMergedOptimizeResponse = {
  /** Echo of request `ratePlan` (default 1). */
  ratePlan?: number;
  billCycle?: {
    intervalStart?: number;
    intervalEnd?: number;
  };
  inputs?: {
    baseline?: { hasConstraints?: boolean };
    constrained?: {
      hasConstraints?: boolean;
      constraintsAppliedToOptimizer?: boolean;
      constraintText?: string | null;
      constraintsByAppliance?: unknown;
    };
  };
  baseline: OptimizeBranchBody;
  constrained: OptimizeBranchBody | null;
  note?: string;
};

export type BuildMergedOptimizeRequest = {
  uuid: string;
  userUuid?: string;
  timezone?: string;
  /** Utility rate plan for merged rates (default 1 on server). */
  ratePlan?: number;
  /** Natural-language constraint; backend merges with optional slider constraints. */
  constraintText?: string;
  /** Slider-style constraints; must match backend `{ constraints: [...] }` wrapper. */
  constraints?: {
    constraints: Array<{
      appliance_id: number;
      load_start_time: string;
      load_end_time: string;
    }>;
  };
};

/** Use unconstrained best-case when no user constraints; otherwise the constrained optimizer branch. */
export function selectOptimizeBranch(res: BuildMergedOptimizeResponse): OptimizeBranchBody {
  return res.constrained ?? res.baseline;
}

/** One bordered “box” in the Insights section (actual / constrained rows). */
export type InsightBox = {
  key: string;
  title: string;
  text: string;
  /** No non-empty insight text for this slot. */
  empty?: boolean;
};

function collectInsightsFromBranch(branch: OptimizeBranchBody | null | undefined): Array<{
  appId: number;
  name: string;
  text: string;
}> {
  const raw = branch?.insights;
  if (!raw || typeof raw !== 'object') return [];
  const appliances = (raw as LoadShiftInsightsPayload).appliances;
  if (!Array.isArray(appliances)) return [];
  const out: Array<{ appId: number; name: string; text: string }> = [];
  for (const row of appliances) {
    if (!row || typeof row !== 'object') continue;
    const appId = Number((row as { appId?: unknown }).appId);
    const name = String((row as { name?: unknown }).name ?? '')
      .replaceAll('_', ' ')
      .trim();
    const text = String((row as { insight?: unknown }).insight ?? '').trim();
    if (!text) continue;
    out.push({
      appId: Number.isFinite(appId) ? appId : out.length,
      name: name || 'Appliance',
      text,
    });
  }
  return out;
}

function padInsightBoxes(
  items: Array<{ appId: number; name: string; text: string }>,
  count: number,
): InsightBox[] {
  const boxes: InsightBox[] = [];
  for (let i = 0; i < count; i++) {
    const item = items[i];
    if (item) {
      boxes.push({
        key: `insight-${item.appId}-${i}`,
        title: item.name,
        text: item.text,
      });
    } else {
      boxes.push({
        key: `insight-empty-${i}`,
        title: 'Insight',
        text: '',
        empty: true,
      });
    }
  }
  return boxes;
}

export type OptimizeDisplayState = {
  billCycleStart: number | null;
  billCycleEnd: number | null;
  totalCurrentCost: number;
  /** Unrestricted optimizer best total (baseline). */
  totalBaselineBestCost: number;
  /** Best total after user constraints; equals baseline when no constrained run. */
  totalConstrainedBestCost: number;
  /** True when API returned a `constrained` branch (user sent constraints). */
  hasConstrainedRun: boolean;
  /** First two non-empty baseline (best-case) load-shift insights. */
  actualInsightBoxes: InsightBox[];
  /** First two non-empty constrained load-shift insights; placeholders when no constrained run. */
  constrainedInsightBoxes: InsightBox[];
  appliances: Array<{
    appId: number;
    name: string;
    insight?: string;
    currentCost: number;
    currentConsumption: number;
    baselineBestCost: number;
    constrainedBestCost: number;
    /** Savings vs current: from constrained run when present, else baseline. */
    costSavings: number;
  }>;
};

export function buildOptimizeDisplayState(res: BuildMergedOptimizeResponse): OptimizeDisplayState {
  const bl = res.baseline;
  const co = res.constrained;
  const constrainedByApp = new Map((co?.appliances ?? []).map((a) => [a.appId, a]));

  const baselineInsightList = collectInsightsFromBranch(bl);
  const constrainedInsightList = collectInsightsFromBranch(co ?? undefined);

  return {
    billCycleStart: res.billCycle?.intervalStart ?? null,
    billCycleEnd: res.billCycle?.intervalEnd ?? null,
    totalCurrentCost: bl.total.current.cost,
    totalBaselineBestCost: bl.total.best.cost,
    totalConstrainedBestCost: co?.total.best.cost ?? bl.total.best.cost,
    hasConstrainedRun: co != null,
    actualInsightBoxes: padInsightBoxes(baselineInsightList, 2),
    constrainedInsightBoxes: padInsightBoxes(co != null ? constrainedInsightList : [], 2),
    appliances: bl.appliances
      .filter((appliance) => appliance.current.consumption > 0)
      .map((appliance) => {
        const c = constrainedByApp.get(appliance.appId);
        const baselineBest = appliance.best.cost;
        const constrainedBest = c?.best.cost ?? baselineBest;
        const insight =
          c?.insight != null && String(c.insight).trim() !== ''
            ? c.insight
            : appliance.insight;
        return {
          appId: appliance.appId,
          name: appliance.name,
          insight,
          currentCost: appliance.current.cost,
          currentConsumption: appliance.current.consumption,
          baselineBestCost: baselineBest,
          constrainedBestCost: constrainedBest,
          costSavings: c?.savings.costSavings ?? appliance.savings.costSavings,
        };
      }),
  };
}

export async function fetchBuildMergedOptimize(
  payload: BuildMergedOptimizeRequest,
  init?: RequestInit,
): Promise<BuildMergedOptimizeResponse> {
  const response = await fetch(`${API_BASE_URL}/api/build-merged-optimize`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
    ...init,
  });

  if (!response.ok) {
    throw new Error(`Build merged optimize failed: ${response.status}`);
  }

  return (await response.json()) as BuildMergedOptimizeResponse;
}
