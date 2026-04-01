import {
  useEffect,
  useId,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type Dispatch,
  type SetStateAction,
} from 'react';
import Card from '@mui/material/Card';
import CardContent from '@mui/material/CardContent';
import Box from '@mui/material/Box';
import Button from '@mui/material/Button';
import CircularProgress from '@mui/material/CircularProgress';
import Collapse from '@mui/material/Collapse';
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
import { ratePlanDisplayName } from '../features/who/ratePlanLabels';
import { defaultTimelineRangeForAppId, syncTimelineForAppIds } from '../features/who/whoSlice';
import { useAppDispatch, useAppSelector } from '../store/hooks';
import { CostApplianceChartCard } from './CostApplianceChartCard';
import { HourScale } from './HourScale';
import { TimelineRangeBar } from './TimelineRangeBar';

const DEFAULT_OPTIMIZER_UUID = '9d1df24f-f902-45ac-b3f4-a711dd57c0a5';

/** Default merged-rate plan (full dashboard + insights). */
const PRIMARY_RATE_PLAN = 1;
/** Extra plans: same constraints as primary; charts only, loaded after the default response. */
const COMPARISON_RATE_PLANS = [9, 6, 7] as const;

type AlternatePlanSlot = {
  ratePlan: number;
  status: 'idle' | 'loading' | 'ok' | 'error';
  data: OptimizeDisplayState | null;
  error: string | null;
};

function initialAlternateSlots(): AlternatePlanSlot[] {
  return COMPARISON_RATE_PLANS.map((ratePlan) => ({
    ratePlan,
    status: 'idle',
    data: null,
    error: null,
  }));
}

const SLIDER_ROW_COLORS = ['#1976d2', '#ef6c00', '#6a1b9a', '#2e7d32', '#7b1fa2'];

const usd = new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' });

function formatUsd(value: number): string {
  return usd.format(value);
}

const COMPARISON_PLAN_SET = new Set<number>(COMPARISON_RATE_PLANS);

function loadComparisonRatePlans(
  base: Parameters<typeof fetchBuildMergedOptimize>[0],
  signal: AbortSignal,
  setSlots: Dispatch<SetStateAction<AlternatePlanSlot[]>>,
) {
  // Always rebuild from COMPARISON_RATE_PLANS so removed plans (e.g. 2) never linger after HMR or code changes.
  setSlots(
    COMPARISON_RATE_PLANS.map((ratePlan) => ({
      ratePlan,
      status: 'loading' as const,
      data: null,
      error: null,
    })),
  );
  for (const ratePlan of COMPARISON_RATE_PLANS) {
    void fetchBuildMergedOptimize({ ...base, ratePlan }, { signal })
      .then((r) => {
        if (signal.aborted) return;
        const display = buildOptimizeDisplayState(r);
        setSlots((prev) =>
          prev.map((s) =>
            s.ratePlan === ratePlan ? { ...s, status: 'ok', data: display, error: null } : s,
          ),
        );
      })
      .catch((err: unknown) => {
        if (signal.aborted) return;
        if (err instanceof DOMException && err.name === 'AbortError') return;
        setSlots((prev) =>
          prev.map((s) =>
            s.ratePlan === ratePlan
              ? {
                  ...s,
                  status: 'error',
                  data: null,
                  error: 'Unable to load optimization for this rate plan.',
                }
              : s,
          ),
        );
      });
  }
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
  const [alternatePlanSlots, setAlternatePlanSlots] = useState<AlternatePlanSlot[]>(initialAlternateSlots);
  const [rateComparisonOpen, setRateComparisonOpen] = useState(false);
  const optimizeSuiteAbortRef = useRef<AbortController | null>(null);
  /** Suppress stale `finally` blocks after a newer optimize run supersedes this one. */
  const optimizeLoadGenerationRef = useRef(0);

  useEffect(() => {
    setAlternatePlanSlots((prev) => {
      const onlyAllowed = prev.filter((s) => COMPARISON_PLAN_SET.has(s.ratePlan));
      if (
        onlyAllowed.length === prev.length &&
        onlyAllowed.length === COMPARISON_RATE_PLANS.length
      ) {
        return prev;
      }
      return initialAlternateSlots();
    });
  }, []);

  useLayoutEffect(() => {
    if (!optimizeData?.appliances.length) {
      dispatch(syncTimelineForAppIds([]));
      return;
    }
    dispatch(syncTimelineForAppIds(optimizeData.appliances.map((a) => a.appId)));
  }, [optimizeData, dispatch]);

  useEffect(() => {
    if (!optimizeData || graphErrorMessage) {
      setRateComparisonOpen(false);
    }
  }, [optimizeData, graphErrorMessage]);

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

    const baseRequest = {
      uuid,
      userUuid: userUuid || undefined,
      timezone,
      ratePlan: PRIMARY_RATE_PLAN,
      ...(analysisInputMode === 'text'
        ? { constraintText: trimmedText }
        : { constraints: { constraints: sliderConstraints } }),
    };

    optimizeSuiteAbortRef.current?.abort();
    const ac = new AbortController();
    optimizeSuiteAbortRef.current = ac;
    const loadGen = ++optimizeLoadGenerationRef.current;

    try {
      const optimizeResult = await fetchBuildMergedOptimize(baseRequest, { signal: ac.signal });
      if (ac.signal.aborted || loadGen !== optimizeLoadGenerationRef.current) return;
      setOptimizeData(buildOptimizeDisplayState(optimizeResult));
      setAnalysisSuccessMessage('Optimization updated with your constraints.');
      loadComparisonRatePlans(baseRequest, ac.signal, setAlternatePlanSlots);
    } catch {
      if (ac.signal.aborted || loadGen !== optimizeLoadGenerationRef.current) return;
      setGraphErrorMessage('Unable to load optimization graph data.');
      setAlternatePlanSlots(initialAlternateSlots());
    } finally {
      if (loadGen === optimizeLoadGenerationRef.current) {
        setIsGraphLoading(false);
        setIsAnalyzing(false);
      }
    }
  };

  useEffect(() => {
    optimizeSuiteAbortRef.current?.abort();
    const ac = new AbortController();
    optimizeSuiteAbortRef.current = ac;
    const loadGen = ++optimizeLoadGenerationRef.current;

    const loadGraphData = async () => {
      setIsGraphLoading(true);
      setGraphErrorMessage(null);
      try {
        const { uuid, userUuid, timezone } = readOptimizerRequestParams();
        const baseRequest = {
          uuid,
          userUuid: userUuid || undefined,
          timezone,
          ratePlan: PRIMARY_RATE_PLAN,
        };
        const result = await fetchBuildMergedOptimize(baseRequest, { signal: ac.signal });
        if (ac.signal.aborted || loadGen !== optimizeLoadGenerationRef.current) return;
        setOptimizeData(buildOptimizeDisplayState(result));
        loadComparisonRatePlans(baseRequest, ac.signal, setAlternatePlanSlots);
      } catch {
        if (ac.signal.aborted || loadGen !== optimizeLoadGenerationRef.current) return;
        setGraphErrorMessage('Unable to load optimization graph data.');
        setAlternatePlanSlots(initialAlternateSlots());
      } finally {
        if (loadGen === optimizeLoadGenerationRef.current && !ac.signal.aborted) {
          setIsGraphLoading(false);
        }
      }
    };

    void loadGraphData();
    return () => {
      ac.abort();
    };
  }, []);

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

        <CostApplianceChartCard
          title="Cost by Appliance"
          billCycleLabel={billCycleLabel}
          optimizeData={optimizeData}
          errorMessage={graphErrorMessage}
        />

        {optimizeData && !graphErrorMessage ? (
          <Stack alignItems="center" sx={{ pt: 0.5 }}>
            <Button
              type="button"
              variant="contained"
              onClick={() => setRateComparisonOpen((open) => !open)}
              aria-expanded={rateComparisonOpen}
              aria-controls="rate-plan-comparison-panel"
              id="rate-plan-comparison-toggle"
              sx={{
                px: 2.5,
                py: 1.25,
                borderRadius: 3,
                textTransform: 'none',
                fontWeight: 600,
                bgcolor: '#5c6bc0',
                position: 'relative',
                overflow: 'hidden',
                animation: 'ratePlanCtaGlow 2.4s ease-in-out infinite',
                '@keyframes ratePlanCtaGlow': {
                  '0%, 100%': {
                    boxShadow: '0 0 0 0 rgba(92, 107, 192, 0.55)',
                    transform: 'scale(1)',
                  },
                  '45%': {
                    boxShadow: '0 0 0 10px rgba(92, 107, 192, 0)',
                    transform: 'scale(1.02)',
                  },
                },
                '@media (prefers-reduced-motion: reduce)': {
                  animation: 'none',
                  '&::after': { display: 'none' },
                },
                '&:hover': {
                  bgcolor: '#3f51b5',
                  animation: 'none',
                  boxShadow: 4,
                },
                '&::after': {
                  content: '""',
                  position: 'absolute',
                  inset: 0,
                  background:
                    'linear-gradient(105deg, transparent 40%, rgba(255,255,255,0.22) 50%, transparent 60%)',
                  transform: 'translateX(-100%)',
                  animation: 'ratePlanShimmer 3.5s ease-in-out infinite',
                },
                '@keyframes ratePlanShimmer': {
                  '0%': { transform: 'translateX(-100%)' },
                  '18%, 100%': { transform: 'translateX(100%)' },
                },
              }}
            >
              <Box component="span" sx={{ position: 'relative', zIndex: 1 }}>
                {rateComparisonOpen ? 'Hide other rate plans' : 'Check savings on other Rate Plan'}
              </Box>
            </Button>
          </Stack>
        ) : null}

        <Collapse in={rateComparisonOpen} timeout="auto">
          <Stack
            spacing={2}
            sx={{ pt: 2 }}
            id="rate-plan-comparison-panel"
            role="region"
            aria-labelledby="rate-plan-comparison-toggle"
          >
            <Typography variant="body2" color="text.secondary" textAlign="center">
              Same usage and constraints as above; bars use each plan&apos;s rates ({COMPARISON_RATE_PLANS.map((id) => ratePlanDisplayName(id)).join(', ')}).
            </Typography>
            {optimizeData && !graphErrorMessage
              ? alternatePlanSlots
                  .filter((slot) => COMPARISON_PLAN_SET.has(slot.ratePlan))
                  .map((slot) => (
                    <CostApplianceChartCard
                      key={slot.ratePlan}
                      title={`Cost by appliance — ${ratePlanDisplayName(slot.ratePlan)}`}
                      optimizeData={slot.status === 'ok' ? slot.data : null}
                      inlineLoading={slot.status === 'loading'}
                      errorMessage={slot.status === 'error' ? slot.error : null}
                      compact
                    />
                  ))
              : null}
          </Stack>
        </Collapse>

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
