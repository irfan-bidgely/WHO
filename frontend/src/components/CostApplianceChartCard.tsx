import { useMemo } from 'react';
import Box from '@mui/material/Box';
import Card from '@mui/material/Card';
import CardContent from '@mui/material/CardContent';
import CircularProgress from '@mui/material/CircularProgress';
import Divider from '@mui/material/Divider';
import Stack from '@mui/material/Stack';
import Typography from '@mui/material/Typography';
import type { OptimizeDisplayState } from '../features/who/buildMergedOptimizeApi';

const usd = new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' });

function formatUsd(value: number): string {
  return usd.format(value);
}

export type CostApplianceChartCardProps = {
  title: string;
  billCycleLabel?: string | null;
  optimizeData: OptimizeDisplayState | null;
  /** Spinner inside the card (e.g. alternate rate plans still loading). */
  inlineLoading?: boolean;
  errorMessage?: string | null;
  /** Slightly tighter layout for secondary charts. */
  compact?: boolean;
};

/**
 * Bar chart: actual vs best (and constrained when present) per appliance — same data as main WHO cost card.
 */
export function CostApplianceChartCard({
  title,
  billCycleLabel,
  optimizeData,
  inlineLoading,
  errorMessage,
  compact = false,
}: CostApplianceChartCardProps) {
  const showConstrainedBar = optimizeData?.hasConstrainedRun ?? false;

  const chartData = useMemo(() => {
    const mapped =
      optimizeData?.appliances
        .map((appliance) => ({
          label: appliance.name.replaceAll('_', ' '),
          actualCost: appliance.currentCost,
          baselineBestCost: appliance.baselineBestCost,
          constrainedBestCost: appliance.constrainedBestCost,
        }))
        .sort((a, b) => {
          const maxA = showConstrainedBar
            ? Math.max(a.actualCost, a.baselineBestCost, a.constrainedBestCost)
            : Math.max(a.actualCost, a.baselineBestCost);
          const maxB = showConstrainedBar
            ? Math.max(b.actualCost, b.baselineBestCost, b.constrainedBestCost)
            : Math.max(b.actualCost, b.baselineBestCost);
          return maxB - maxA;
        })
        .slice(0, 5) ?? [];
    const maxCost = Math.max(
      1,
      ...mapped.map((item) =>
        showConstrainedBar
          ? Math.max(item.actualCost, item.baselineBestCost, item.constrainedBestCost)
          : Math.max(item.actualCost, item.baselineBestCost),
      ),
    );
    const maxBarHeight = compact ? 120 : 160;
    const barHeight = (value: number) =>
      value > 0 ? Math.max(6, (value / maxCost) * maxBarHeight) : 0;
    return mapped.map((item) => ({
      label: item.label,
      actual: barHeight(item.actualCost),
      baselineBest: barHeight(item.baselineBestCost),
      constrainedBest: showConstrainedBar ? barHeight(item.constrainedBestCost) : 0,
      actualCost: item.actualCost,
      baselineBestCost: item.baselineBestCost,
      constrainedBestCost: item.constrainedBestCost,
    }));
  }, [optimizeData, showConstrainedBar, compact]);

  const barStackHeight = compact ? 120 : 160;

  return (
    <Card elevation={0} sx={{ borderRadius: 3 }}>
      <CardContent sx={{ p: { xs: 2, md: compact ? 2 : 3 } }}>
        <Stack spacing={2}>
          <Stack direction="row" justifyContent="space-between" alignItems="center" flexWrap="wrap" gap={1}>
            <Typography variant={compact ? 'subtitle1' : 'h6'} color="text.primary">
              {title}
            </Typography>
            {billCycleLabel ? (
              <Typography variant="caption" color="text.secondary">
                Billing Cycle: {billCycleLabel}
              </Typography>
            ) : null}
          </Stack>

          {inlineLoading ? (
            <Stack alignItems="center" justifyContent="center" sx={{ minHeight: 160 }} spacing={1}>
              <CircularProgress size={compact ? 32 : 40} />
              <Typography variant="caption" color="text.secondary">
                Loading rate plan…
              </Typography>
            </Stack>
          ) : errorMessage ? (
            <Typography variant="body2" color="error.main">
              {errorMessage}
            </Typography>
          ) : (
            <Stack direction="row" spacing={3} alignItems="flex-end" sx={{ minHeight: compact ? 180 : 220 }}>
              <Stack spacing={3} justifyContent="flex-end" sx={{ minWidth: 36, pb: 4 }}>
                {['$200', '$100', '$0'].map((label) => (
                  <Typography key={label} variant="caption" color="text.secondary">
                    {label}
                  </Typography>
                ))}
              </Stack>

              <Divider orientation="vertical" flexItem />

              <Box
                sx={{
                  flex: 1,
                  minWidth: 0,
                  display: 'flex',
                  flexDirection: { xs: 'column', sm: 'row' },
                  alignItems: { xs: 'stretch', sm: 'flex-end' },
                  gap: { xs: 1.5, sm: 2 },
                }}
              >
                <Stack
                  direction="row"
                  spacing={{ xs: 2, md: compact ? 2 : 4 }}
                  alignItems="flex-end"
                  sx={{ pb: 1, flex: 1, minWidth: 0 }}
                >
                  {chartData.map((item) => (
                    <Stack key={item.label} spacing={1} alignItems="center" sx={{ maxWidth: 140 }}>
                      <Stack direction="row" spacing={0.35} alignItems="flex-end" sx={{ height: barStackHeight }}>
                        <Box
                          sx={{
                            width: showConstrainedBar ? 12 : 16,
                            height: item.actual,
                            bgcolor: '#1976d2',
                            borderRadius: 0.5,
                          }}
                        />
                        <Box
                          sx={{
                            width: showConstrainedBar ? 12 : 16,
                            height: item.baselineBest,
                            bgcolor: '#2e7d32',
                            borderRadius: 0.5,
                          }}
                        />
                        {showConstrainedBar ? (
                          <Box
                            sx={{
                              width: 12,
                              height: item.constrainedBest,
                              bgcolor: '#ed6c02',
                              borderRadius: 0.5,
                            }}
                          />
                        ) : null}
                      </Stack>
                      <Typography variant="caption" color="text.secondary" textAlign="center">
                        {item.label}
                      </Typography>
                      <Typography
                        variant="caption"
                        color="text.secondary"
                        textAlign="center"
                        sx={{ lineHeight: 1.35 }}
                      >
                        {showConstrainedBar ? (
                          <>
                            {formatUsd(item.actualCost)} · {formatUsd(item.baselineBestCost)} ·{' '}
                            {formatUsd(item.constrainedBestCost)}
                          </>
                        ) : (
                          <>
                            {formatUsd(item.actualCost)} {'->'} {formatUsd(item.baselineBestCost)}
                          </>
                        )}
                      </Typography>
                    </Stack>
                  ))}
                </Stack>

                <Stack
                  direction="column"
                  spacing={0.5}
                  sx={{
                    flexShrink: 0,
                    pb: 1,
                    alignSelf: { xs: 'center', sm: 'flex-end' },
                    pl: { xs: 0, sm: 2 },
                    borderLeft: { xs: 'none', sm: (theme) => `1px solid ${theme.palette.divider}` },
                  }}
                >
                  <Stack direction="row" spacing={2} alignItems="center" sx={{ flexWrap: 'wrap', gap: 1 }}>
                    <Stack direction="row" spacing={1} alignItems="center">
                      <Box sx={{ width: 10, height: 10, bgcolor: '#1976d2', flexShrink: 0 }} />
                      <Typography variant="caption" color="text.secondary">
                        Actual
                      </Typography>
                    </Stack>
                    <Stack direction="row" spacing={1} alignItems="center">
                      <Box sx={{ width: 10, height: 10, bgcolor: '#2e7d32', flexShrink: 0 }} />
                      <Typography variant="caption" color="text.secondary">
                        Best
                      </Typography>
                    </Stack>
                    {showConstrainedBar ? (
                      <Stack direction="row" spacing={1} alignItems="center">
                        <Box sx={{ width: 10, height: 10, bgcolor: '#ed6c02', flexShrink: 0 }} />
                        <Typography variant="caption" color="text.secondary">
                          Constrained
                        </Typography>
                      </Stack>
                    ) : null}
                  </Stack>
                </Stack>
              </Box>
            </Stack>
          )}
        </Stack>
      </CardContent>
    </Card>
  );
}
