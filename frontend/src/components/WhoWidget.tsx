import { useEffect, useId, useLayoutEffect, useMemo, useState } from 'react';
import Card from '@mui/material/Card';
import CardContent from '@mui/material/CardContent';
import Box from '@mui/material/Box';
import Button from '@mui/material/Button';
import CircularProgress from '@mui/material/CircularProgress';
import Divider from '@mui/material/Divider';
import Paper from '@mui/material/Paper';
import Stack from '@mui/material/Stack';
import Tab from '@mui/material/Tab';
import Tabs from '@mui/material/Tabs';
import TextField from '@mui/material/TextField';
import Typography from '@mui/material/Typography';
import {
  buildOptimizeDisplayState,
  fetchBuildMergedOptimize,
  type OptimizeDisplayState,
} from '../features/who/buildMergedOptimizeApi';
import { defaultTimelineRangeForAppId, syncTimelineForAppIds } from '../features/who/whoSlice';
import { useAppDispatch, useAppSelector } from '../store/hooks';
import { HourScale } from './HourScale';
import { TimelineRangeBar } from './TimelineRangeBar';

const DEFAULT_OPTIMIZER_UUID = '9d1df24f-f902-45ac-b3f4-a711dd57c0a5';

const SLIDER_ROW_COLORS = ['#1976d2', '#ef6c00', '#6a1b9a', '#2e7d32', '#7b1fa2'];

const usd = new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' });

function formatUsd(value: number): string {
  return usd.format(value);
}

function readOptimizerRequestParams() {
  const uuid =
    (import.meta.env.VITE_OPTIMIZER_UUID as string | undefined)?.trim() || DEFAULT_OPTIMIZER_UUID;
  const userUuid = (import.meta.env.VITE_OPTIMIZER_USER_UUID as string | undefined)?.trim() ?? '';
  const timezone = (import.meta.env.VITE_OPTIMIZER_TIMEZONE as string | undefined)?.trim() ?? 'UTC';
  return { uuid, userUuid, timezone };
}

/**
 * Primary SPA surface: WHO content and state wired through Redux Toolkit.
 */
export function WhoWidget() {
  const titleId = useId();
  const dispatch = useAppDispatch();
  const title = useAppSelector((s) => s.who.title);
  const timeline = useAppSelector((s) => s.who.timeline);
  const [lifestyleInput, setLifestyleInput] = useState('');
  const [analysisInputMode, setAnalysisInputMode] = useState<'text' | 'sliders'>('text');
  const [isAnalyzing, setIsAnalyzing] = useState(false);
  const [isGraphLoading, setIsGraphLoading] = useState(true);
  const [graphErrorMessage, setGraphErrorMessage] = useState<string | null>(null);
  const [analysisSuccessMessage, setAnalysisSuccessMessage] = useState<string | null>(null);
  const [analysisErrorMessage, setAnalysisErrorMessage] = useState<string | null>(null);
  const [optimizeData, setOptimizeData] = useState<OptimizeDisplayState | null>(null);

  useLayoutEffect(() => {
    if (!optimizeData?.appliances.length) {
      dispatch(syncTimelineForAppIds([]));
      return;
    }
    dispatch(syncTimelineForAppIds(optimizeData.appliances.map((a) => a.appId)));
  }, [optimizeData, dispatch]);

  const pctToTimeString = (pct: number): string => {
    const totalMinutes = Math.round((pct / 100) * 24 * 60);
    if (totalMinutes >= 24 * 60) {
      return '24:00';
    }
    const normalized = ((totalMinutes % (24 * 60)) + 24 * 60) % (24 * 60);
    const hh = Math.floor(normalized / 60)
      .toString()
      .padStart(2, '0');
    const mm = (normalized % 60).toString().padStart(2, '0');
    return `${hh}:${mm}`;
  };

  const handleAnalyzeConstraints = async () => {
    const trimmedText = lifestyleInput.trim();
    const sliderConstraints = (optimizeData?.appliances ?? []).map((a) => {
      const range = timeline[a.appId] ?? defaultTimelineRangeForAppId(a.appId);
      return {
        appliance_id: a.appId,
        load_start_time: pctToTimeString(range.startPct),
        load_end_time: pctToTimeString(range.endPct),
      };
    });

    if (analysisInputMode === 'text' && !trimmedText) {
      setAnalysisSuccessMessage(null);
      setAnalysisErrorMessage('Please enter a constraint first.');
      return;
    }

    if (analysisInputMode === 'sliders' && sliderConstraints.length === 0) {
      setAnalysisSuccessMessage(null);
      setAnalysisErrorMessage('Load appliance data first (same appliances as the cost chart).');
      return;
    }

    setIsAnalyzing(true);
    setAnalysisSuccessMessage(null);
    setAnalysisErrorMessage(null);

    const { uuid, userUuid, timezone } = readOptimizerRequestParams();
    setIsGraphLoading(true);
    setGraphErrorMessage(null);

    try {
      const optimizeResult = await fetchBuildMergedOptimize({
        uuid,
        userUuid: userUuid || undefined,
        timezone,
        ...(analysisInputMode === 'text'
          ? { constraintText: trimmedText }
          : { constraints: { constraints: sliderConstraints } }),
      });
      setOptimizeData(buildOptimizeDisplayState(optimizeResult));
      setAnalysisSuccessMessage('Optimization updated with your constraints.');
    } catch {
      setGraphErrorMessage('Unable to load optimization graph data.');
    } finally {
      setIsGraphLoading(false);
      setIsAnalyzing(false);
    }
  };

  useEffect(() => {
    let cancelled = false;
    const loadGraphData = async () => {
      setIsGraphLoading(true);
      setGraphErrorMessage(null);
      try {
        const { uuid, userUuid, timezone } = readOptimizerRequestParams();
        const result = await fetchBuildMergedOptimize({
          uuid,
          userUuid: userUuid || undefined,
          timezone,
        });
        if (cancelled) return;
        setOptimizeData(buildOptimizeDisplayState(result));
      } catch {
        if (cancelled) return;
        setGraphErrorMessage('Unable to load optimization graph data.');
      } finally {
        if (!cancelled) setIsGraphLoading(false);
      }
    };

    void loadGraphData();
    return () => {
      cancelled = true;
    };
  }, []);

  const showConstrainedBar = optimizeData?.hasConstrainedRun ?? false;

  const chartData = useMemo(
    () => {
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
      const maxBarHeight = 160;
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
    },
    [optimizeData, showConstrainedBar],
  );

  const billCycleLabel = useMemo(() => {
    if (!optimizeData?.billCycleStart || !optimizeData.billCycleEnd) return null;
    const start = new Date(optimizeData.billCycleStart * 1000);
    const end = new Date(optimizeData.billCycleEnd * 1000);
    const fmt = new Intl.DateTimeFormat('en-US', { day: '2-digit', month: 'short', year: 'numeric' });
    return `${fmt.format(start)} - ${fmt.format(end)}`;
  }, [optimizeData]);

  const emptyInsightPair = useMemo(
    () => [
      { key: 'placeholder-0', title: 'Insight', text: '', empty: true as const },
      { key: 'placeholder-1', title: 'Insight', text: '', empty: true as const },
    ],
    [],
  );

  const actualInsightRow = optimizeData?.actualInsightBoxes ?? emptyInsightPair;
  const constrainedInsightRow = optimizeData?.constrainedInsightBoxes ?? emptyInsightPair;

  const summaryInsights = useMemo(
    () => {
      if (!optimizeData) {
        return [
          { key: 's0', title: 'Optimization', value: 'Loading...', color: '#2e7d32' },
          { key: 's1', title: 'Cost', value: 'Loading...', color: '#ef6c00' },
          { key: 's2', title: 'Appliances', value: 'Loading...', color: '#6a1b9a' },
        ];
      }
      const topSavings = [...optimizeData.appliances]
        .sort((a, b) => b.costSavings - a.costSavings)
        .slice(0, 3);
      const first = topSavings[0];
      const second = topSavings[1];
      const third = topSavings[2];
      return [
        {
          key: 'top-saver',
          title: `Top Saver: ${first?.name?.replaceAll('_', ' ') ?? 'N/A'}`,
          value: `Save ${formatUsd(Number(first?.costSavings ?? 0))}`,
          color: '#2e7d32',
        },
        {
          key: 'totals',
          title: optimizeData.hasConstrainedRun ? 'Total: Actual / Best / Constrained' : 'Total: Actual / Best',
          value: optimizeData.hasConstrainedRun
            ? `${formatUsd(optimizeData.totalCurrentCost)} · ${formatUsd(optimizeData.totalBaselineBestCost)} · ${formatUsd(optimizeData.totalConstrainedBestCost)}`
            : `${formatUsd(optimizeData.totalCurrentCost)} -> ${formatUsd(optimizeData.totalBaselineBestCost)}`,
          color: '#ef6c00',
        },
        {
          key: 'next-saver',
          title: `Next: ${second?.name?.replaceAll('_', ' ') ?? third?.name?.replaceAll('_', ' ') ?? 'N/A'}`,
          value: `Save ${formatUsd(Number((second ?? third)?.costSavings ?? 0))}`,
          color: '#6a1b9a',
        },
      ];
    },
    [optimizeData],
  );

  return (
    <Paper
      component="section"
      elevation={0}
      sx={{
        p: { xs: 2, md: 3 },
        maxWidth: 1120,
        mx: 'auto',
        mt: 2,
        bgcolor: 'transparent',
        position: 'relative',
      }}
      aria-labelledby={titleId}
    >
      {isGraphLoading ? (
        <Box
          sx={{
            position: 'absolute',
            inset: 0,
            zIndex: (theme) => theme.zIndex.modal,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            bgcolor: 'rgba(255,255,255,0.78)',
            backdropFilter: 'blur(4px)',
            borderRadius: 0,
          }}
          aria-busy="true"
          aria-live="polite"
        >
          <Stack alignItems="center" spacing={1.5}>
            <CircularProgress size={48} thickness={4} />
            <Typography variant="body2" color="text.secondary">
              Loading optimization…
            </Typography>
          </Stack>
        </Box>
      ) : null}
      <Stack spacing={3}>
        <Typography id={titleId} variant="h4" component="h1" color="text.primary">
          {title}
        </Typography>

        <Card elevation={0} sx={{ borderRadius: 3 }}>
          <CardContent sx={{ p: { xs: 2, md: 3 } }}>
            <Stack spacing={2}>
              <Stack direction="row" justifyContent="space-between" alignItems="center">
                <Typography variant="h6" color="text.primary">
                  Cost by Appliance
                </Typography>
                {billCycleLabel ? (
                  <Typography variant="body2" color="text.secondary">
                    Billing Cycle: {billCycleLabel}
                  </Typography>
                ) : null}
              </Stack>

              <Stack direction="row" spacing={3} alignItems="flex-end" sx={{ minHeight: 220 }}>
                <Stack spacing={3} justifyContent="flex-end" sx={{ minWidth: 40, pb: 4 }}>
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
                    spacing={{ xs: 2, md: 4 }}
                    alignItems="flex-end"
                    sx={{ pb: 1, flex: 1, minWidth: 0 }}
                  >
                    {chartData.map((item) => (
                      <Stack key={item.label} spacing={1} alignItems="center" sx={{ maxWidth: 140 }}>
                        <Stack direction="row" spacing={0.35} alignItems="flex-end" sx={{ height: 160 }}>
                          <Box
                            sx={{
                              width: showConstrainedBar ? 14 : 18,
                              height: item.actual,
                              bgcolor: '#1976d2',
                              borderRadius: 0.5,
                            }}
                          />
                          <Box
                            sx={{
                              width: showConstrainedBar ? 14 : 18,
                              height: item.baselineBest,
                              bgcolor: '#2e7d32',
                              borderRadius: 0.5,
                            }}
                          />
                          {showConstrainedBar ? (
                            <Box
                              sx={{
                                width: 14,
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
                        <Typography variant="caption" color="text.secondary" textAlign="center" sx={{ lineHeight: 1.35 }}>
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
                        <Box sx={{ width: 12, height: 12, bgcolor: '#1976d2', flexShrink: 0 }} />
                        <Typography variant="body2" color="text.secondary">
                          Actual
                        </Typography>
                      </Stack>
                      <Stack direction="row" spacing={1} alignItems="center">
                        <Box sx={{ width: 12, height: 12, bgcolor: '#2e7d32', flexShrink: 0 }} />
                        <Typography variant="body2" color="text.secondary">
                          Best
                        </Typography>
                      </Stack>
                      {showConstrainedBar ? (
                        <Stack direction="row" spacing={1} alignItems="center">
                          <Box sx={{ width: 12, height: 12, bgcolor: '#ed6c02', flexShrink: 0 }} />
                          <Typography variant="body2" color="text.secondary">
                            Constrained
                          </Typography>
                        </Stack>
                      ) : null}
                    </Stack>
                  </Stack>
                </Box>
              </Stack>
            </Stack>
            {graphErrorMessage ? (
              <Typography variant="caption" color="error.main">
                {graphErrorMessage}
              </Typography>
            ) : null}
          </CardContent>
        </Card>

        <Stack spacing={0.5}>
            <Typography variant="subtitle2" color="text.secondary">
              All summaries
            </Typography>
            <Stack direction={{ xs: 'column', md: 'row' }} spacing={2}>
              {summaryInsights.map((item) => (
                <Card
                  key={item.key}
                  elevation={0}
                  sx={{
                    flex: 1,
                    borderRadius: 2,
                    border: '1px solid',
                    borderColor: 'divider',
                  }}
                >
                  <CardContent sx={{ py: 2 }}>
                    <Typography variant="body2" color="text.secondary">
                      {item.title}
                    </Typography>
                    <Typography variant="h6" sx={{ mt: 1, color: item.color }}>
                      {item.value}
                    </Typography>
                  </CardContent>
                </Card>
              ))}
            </Stack>
          </Stack>

        <Stack spacing={2}>
          <Typography variant="h6" color="text.primary">
            Insights
          </Typography>



          <Stack spacing={0.5}>
            <Typography variant="subtitle2" color="text.secondary">
              Best case insights
            </Typography>
            <Stack direction={{ xs: 'column', sm: 'row' }} spacing={2}>
              {actualInsightRow.map((box) => (
                <Card
                  key={`actual-${box.key}`}
                  elevation={0}
                  sx={{
                    flex: 1,
                    borderRadius: 2,
                    border: '1px solid',
                    borderColor: 'divider',
                  }}
                >
                  <CardContent sx={{ py: 2 }}>
                    <Typography variant="body2" color="text.secondary">
                      {box.empty ? '—' : box.title}
                    </Typography>
                    <Typography variant="body1" sx={{ mt: 1, color: 'text.primary' }}>
                      {box.empty ? 'No insight for this slot yet.' : box.text}
                    </Typography>
                  </CardContent>
                </Card>
              ))}
            </Stack>
          </Stack>

          {optimizeData?.hasConstrainedRun ? (
            <Stack spacing={0.5}>
              <Typography variant="subtitle2" color="text.secondary">
                Constrained insights
              </Typography>
              <Stack direction={{ xs: 'column', sm: 'row' }} spacing={2}>
                {constrainedInsightRow.map((box) => (
                  <Card
                    key={`constrained-${box.key}`}
                    elevation={0}
                    sx={{
                      flex: 1,
                      borderRadius: 2,
                      border: '1px solid',
                      borderColor: 'divider',
                    }}
                  >
                    <CardContent sx={{ py: 2 }}>
                      <Typography variant="body2" color="text.secondary">
                        {box.empty ? '—' : box.title}
                      </Typography>
                      <Typography variant="body1" sx={{ mt: 1, color: 'text.primary' }}>
                        {box.empty ? 'No insight for this slot yet.' : box.text}
                      </Typography>
                    </CardContent>
                  </Card>
                ))}
              </Stack>
            </Stack>
          ) : null}

          
        </Stack>

        <Card elevation={0} sx={{ borderRadius: 3 }}>
          <CardContent sx={{ p: { xs: 2, md: 3 } }}>
            <Stack spacing={2}>
              <Typography variant="h6" color="text.primary">
                Tell us your lifestyle
              </Typography>

              <Tabs
                value={analysisInputMode}
                onChange={(_, newValue) => setAnalysisInputMode(newValue as 'text' | 'sliders')}
                aria-label="Constraint input mode"
                sx={{ borderBottom: 1, borderColor: 'divider', minHeight: 42 }}
              >
                <Tab
                  label="Text"
                  value="text"
                  id="lifestyle-tab-text"
                  aria-controls="lifestyle-panel-text"
                />
                <Tab
                  label="Sliders"
                  value="sliders"
                  id="lifestyle-tab-sliders"
                  aria-controls="lifestyle-panel-sliders"
                />
              </Tabs>

              {analysisInputMode === 'text' ? (
                <Box role="tabpanel" id="lifestyle-panel-text" aria-labelledby="lifestyle-tab-text" sx={{ pt: 1 }}>
                  <Box
                    sx={{
                      p: 2,
                      borderRadius: 2,
                      border: '1.5px solid #5c6bc0',
                      bgcolor: '#f7f9ff',
                    }}
                  >
                    <TextField
                      value={lifestyleInput}
                      onChange={(event) => setLifestyleInput(event.target.value)}
                      fullWidth
                      multiline
                      minRows={4}
                      variant="standard"
                      placeholder='✨ Start typing... "Charge EV before 7 AM"'
                      InputProps={{ disableUnderline: true }}
                    />
                  </Box>
                </Box>
              ) : (
                <Box
                  role="tabpanel"
                  id="lifestyle-panel-sliders"
                  aria-labelledby="lifestyle-tab-sliders"
                  sx={{ pt: 1 }}
                  hidden={false}
                >
                  <Stack spacing={2.5}>
                    <Typography variant="body2" color="text.secondary">
                      Drag to move • Resize edges • Snaps to hour on release
                    </Typography>

                    <Stack direction="row" spacing={2} alignItems="flex-end">
                      <Box sx={{ width: 72, flexShrink: 0 }} aria-hidden />
                      <Box sx={{ flex: 1, minWidth: 0 }}>
                        <HourScale />
                      </Box>
                    </Stack>

                    <Stack spacing={2}>
                      {(optimizeData?.appliances ?? []).map((app, index) => (
                        <TimelineRangeBar
                          key={app.appId}
                          label={app.name.replaceAll('_', ' ')}
                          color={SLIDER_ROW_COLORS[index % SLIDER_ROW_COLORS.length]}
                          applianceId={app.appId}
                        />
                      ))}
                      {!isGraphLoading && !graphErrorMessage && (optimizeData?.appliances.length ?? 0) === 0 ? (
                        <Typography variant="body2" color="text.secondary">
                          No appliances with usage in this bill cycle; sliders appear when the cost chart has data.
                        </Typography>
                      ) : null}
                    </Stack>
                  </Stack>
                </Box>
              )}

              <Stack direction="row" justifyContent="space-between" alignItems="center" flexWrap="wrap" gap={1}>
                <Typography variant="caption" color="text.secondary">
                  {analysisInputMode === 'text'
                    ? 'Analyze uses your text constraint only.'
                    : 'Analyze uses your slider windows only.'}
                </Typography>
                <Button
                  type="button"
                  variant="contained"
                  sx={{ px: 3, py: 1.2, borderRadius: 2, bgcolor: '#5c6bc0' }}
                  onClick={handleAnalyzeConstraints}
                  disabled={isAnalyzing}
                >
                  {isAnalyzing ? 'Analyzing...' : 'Analyze \u2192'}
                </Button>
              </Stack>
              {analysisErrorMessage ? (
                <Typography variant="caption" color="error.main">
                  {analysisErrorMessage}
                </Typography>
              ) : null}
              {analysisSuccessMessage ? (
                <Typography variant="caption" color="success.main">
                  {analysisSuccessMessage}
                </Typography>
              ) : null}
            </Stack>
          </CardContent>
        </Card>
      </Stack>
    </Paper>
  );
}
