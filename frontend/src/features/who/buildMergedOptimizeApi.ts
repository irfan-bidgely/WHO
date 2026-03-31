const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://127.0.0.1:5000';

export type BuildMergedOptimizeResponse = {
  total: {
    current: { cost: number; consumption: number };
    shiftableSavings: { costSavings: number; consumptionSavings: number };
    best: { cost: number; consumption: number };
  };
  appliances: Array<{
    appId: number;
    name: string;
    current: { cost: number; consumption: number };
    savings: { costSavings: number; consumptionSavings: number };
    best: { cost: number; consumption: number };
  }>;
};

type BuildMergedOptimizeRequest = {
  uuid: string;
  userUuid?: string;
  timezone?: string;
};

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
