import type { BlockConstraints } from './whoSlice';

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://127.0.0.1:5000';

type AnalyzeConstraintRequest = {
  constraintText: string;
};

type AnalyzeConstraintResponse = {
  applianceConstraints: Array<{
    applianceId: number;
    blockConstraints: BlockConstraints;
  }>;
};

export async function analyzeConstraint(
  payload: AnalyzeConstraintRequest,
): Promise<AnalyzeConstraintResponse> {
  const response = await fetch(`${API_BASE_URL}/analyze-constraint`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });

  if (!response.ok) {
    throw new Error(`Constraint analysis failed: ${response.status}`);
  }

  return (await response.json()) as AnalyzeConstraintResponse;
}
