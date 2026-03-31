const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://127.0.0.1:5000';

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
  insights?: unknown;
};

export type BuildMergedOptimizeResponse = {
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

  return {
    billCycleStart: res.billCycle?.intervalStart ?? null,
    billCycleEnd: res.billCycle?.intervalEnd ?? null,
    totalCurrentCost: bl.total.current.cost,
    totalBaselineBestCost: bl.total.best.cost,
    totalConstrainedBestCost: co?.total.best.cost ?? bl.total.best.cost,
    hasConstrainedRun: co != null,
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
): Promise<BuildMergedOptimizeResponse> {
  const response = await fetch(`${API_BASE_URL}/api/build-merged-optimize`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });

  if (!response.ok) {
    throw new Error(`Build merged optimize failed: ${response.status}`);
  }

  return (await response.json()) as BuildMergedOptimizeResponse;
}
