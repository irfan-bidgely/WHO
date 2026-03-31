import { useEffect, useId, useMemo, useState } from 'react';
import Card from '@mui/material/Card';
import CardContent from '@mui/material/CardContent';
import Box from '@mui/material/Box';
import Button from '@mui/material/Button';
import Divider from '@mui/material/Divider';
import Paper from '@mui/material/Paper';
import Stack from '@mui/material/Stack';
import TextField from '@mui/material/TextField';
import Typography from '@mui/material/Typography';
import { analyzeConstraint } from '../features/who/analyzeConstraintApi';
import { fetchBuildMergedOptimize } from '../features/who/buildMergedOptimizeApi';
import { applyConstraintWindow, type TimelineApplianceId } from '../features/who/whoSlice';
import { useAppDispatch, useAppSelector } from '../store/hooks';
import { HourScale } from './HourScale';
import { TimelineRangeBar } from './TimelineRangeBar';

const DEFAULT_OPTIMIZER_UUID = '9d1df24f-f902-45ac-b3f4-a711dd57c0a5';

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
  const [optimizeData, setOptimizeData] = useState<{
    totalCurrentCost: number;
    totalBestCost: number;
    appliances: Array<{ appId: number; name: string; currentCost: number; bestCost: number; costSavings: number }>;
  } | null>(null);
  const applianceIdToTimelineId: Record<number, TimelineApplianceId | undefined> = {
    18: 'ev',
    4: 'ac',
    30: 'laundry',
  };
  const timelineIdToApplianceId: Record<TimelineApplianceId, number> = {
    ev: 18,
    ac: 4,
    laundry: 30,
  };

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
    const sliderConstraints = (Object.entries(timeline) as Array<[TimelineApplianceId, typeof timeline.ev]>).map(
      ([timelineId, range]) => ({
        appliance_id: timelineIdToApplianceId[timelineId],
        load_start_time: pctToTimeString(range.startPct),
        load_end_time: pctToTimeString(range.endPct),
      }),
    );

    if (analysisInputMode === 'text' && !trimmedText) {
      setAnalysisSuccessMessage(null);
      setAnalysisErrorMessage('Please enter a constraint first.');
      return;
    }

    setIsAnalyzing(true);
    setAnalysisSuccessMessage(null);
    setAnalysisErrorMessage(null);
    try {
      const result = await analyzeConstraint(
        analysisInputMode === 'text'
          ? { constraintText: trimmedText }
          : { constraints: sliderConstraints },
      );

      let appliedCount = 0;
      for (const appliance of result.applianceConstraints) {
        const timelineId = applianceIdToTimelineId[appliance.applianceId];
        if (!timelineId) continue;
        const firstWindow = appliance.blockConstraints.allowedWindows?.[0];
        if (!firstWindow) continue;
        dispatch(applyConstraintWindow({ id: timelineId, window: firstWindow }));
        appliedCount += 1;
      }

      if (appliedCount === 0) {
        setAnalysisErrorMessage('No allowed window found in the analyzed constraints.');
      } else {
        setAnalysisSuccessMessage(`Applied constraints to ${appliedCount} appliance(s).`);
      }
    } catch {
      setAnalysisErrorMessage('Could not analyze constraints. Please try again.');
    } finally {
      setIsAnalyzing(false);
    }
  };

  useEffect(() => {
    let cancelled = false;
    const loadGraphData = async () => {
      setIsGraphLoading(true);
      setGraphErrorMessage(null);
      try {
        const uuid =
          (import.meta.env.VITE_OPTIMIZER_UUID as string | undefined)?.trim() || DEFAULT_OPTIMIZER_UUID;
        const userUuid = (import.meta.env.VITE_OPTIMIZER_USER_UUID as string | undefined)?.trim() ?? '';
        const timezone = (import.meta.env.VITE_OPTIMIZER_TIMEZONE as string | undefined)?.trim() ?? 'UTC';
        const result = await fetchBuildMergedOptimize({
          uuid,
          userUuid: userUuid || undefined,
          timezone,
        });
        if (cancelled) return;
        setOptimizeData({
          totalCurrentCost: result.total.current.cost,
          totalBestCost: result.total.best.cost,
          appliances: result.appliances.map((appliance) => ({
            appId: appliance.appId,
            name: appliance.name,
            currentCost: appliance.current.cost,
            bestCost: appliance.best.cost,
            costSavings: appliance.savings.costSavings,
          })),
        });
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

  const chartData = useMemo(
    () => {
      const mapped =
        optimizeData?.appliances
          .map((appliance) => ({
            label: appliance.name.replaceAll('_', ' '),
            actualCost: appliance.currentCost,
            optimizedCost: appliance.bestCost,
          }))
          .sort(
            (a, b) =>
              Math.max(b.actualCost, b.optimizedCost) - Math.max(a.actualCost, a.optimizedCost),
          )
          .slice(0, 5) ?? [];
      const maxCost = Math.max(1, ...mapped.map((item) => Math.max(item.actualCost, item.optimizedCost)));
      const maxBarHeight = 160;
      return mapped.map((item) => ({
        label: item.label,
        actual:
          item.actualCost > 0
            ? Math.max(6, (item.actualCost / maxCost) * maxBarHeight)
            : 0,
        optimized:
          item.optimizedCost > 0
            ? Math.max(6, (item.optimizedCost / maxCost) * maxBarHeight)
            : 0,
        actualCost: item.actualCost,
        optimizedCost: item.optimizedCost,
      }));
    },
    [optimizeData],
  );

  const insights = useMemo(
    () => {
      if (!optimizeData) {
        return [
          { title: 'Optimization', value: 'Loading...', color: '#2e7d32' },
          { title: 'Cost', value: 'Loading...', color: '#ef6c00' },
          { title: 'Appliances', value: 'Loading...', color: '#6a1b9a' },
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
          title: `Top Saver: ${first?.name?.replaceAll('_', ' ') ?? 'N/A'}`,
          value: `Save Rs ${Number(first?.costSavings ?? 0).toFixed(2)}`,
          color: '#2e7d32',
        },
        {
          title: 'Total Current vs Best',
          value: `Rs ${optimizeData.totalCurrentCost.toFixed(2)} -> Rs ${optimizeData.totalBestCost.toFixed(2)}`,
          color: '#ef6c00',
        },
        {
          title: `Next: ${second?.name?.replaceAll('_', ' ') ?? third?.name?.replaceAll('_', ' ') ?? 'N/A'}`,
          value: `Save Rs ${Number((second ?? third)?.costSavings ?? 0).toFixed(2)}`,
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
      sx={{ p: { xs: 2, md: 3 }, maxWidth: 1120, mx: 'auto', mt: 2, bgcolor: 'transparent' }}
      aria-labelledby={titleId}
    >
      <Stack spacing={3}>
        <Typography id={titleId} variant="h4" component="h1" color="text.primary">
          {title}
        </Typography>

        <Card elevation={0} sx={{ borderRadius: 3 }}>
          <CardContent sx={{ p: { xs: 2, md: 3 } }}>
            <Stack spacing={2}>
              <Typography variant="h6" color="text.primary">
                Cost by Appliance
              </Typography>

              <Stack direction="row" spacing={3} alignItems="flex-end" sx={{ minHeight: 220 }}>
                <Stack spacing={3} justifyContent="flex-end" sx={{ minWidth: 40, pb: 4 }}>
                  {['₹200', '₹100', '₹0'].map((label) => (
                    <Typography key={label} variant="caption" color="text.secondary">
                      {label}
                    </Typography>
                  ))}
                </Stack>

                <Divider orientation="vertical" flexItem />

                <Stack direction="row" spacing={{ xs: 3, md: 6 }} alignItems="flex-end" sx={{ pb: 1 }}>
                  {chartData.map((item) => (
                    <Stack key={item.label} spacing={1} alignItems="center">
                      <Stack direction="row" spacing={0.5} alignItems="flex-end" sx={{ height: 160 }}>
                        <Box sx={{ width: 20, height: item.actual, bgcolor: '#1976d2', borderRadius: 0.5 }} />
                        <Box sx={{ width: 20, height: item.optimized, bgcolor: '#2e7d32', borderRadius: 0.5 }} />
                      </Stack>
                      <Typography variant="caption" color="text.secondary">
                        {item.label}
                      </Typography>
                      <Typography variant="caption" color="text.secondary">
                        Rs {item.actualCost.toFixed(2)} {'->'} Rs {item.optimizedCost.toFixed(2)}
                      </Typography>
                    </Stack>
                  ))}
                </Stack>

                <Stack spacing={1} sx={{ ml: 'auto' }}>
                  <Stack direction="row" spacing={1} alignItems="center">
                    <Box sx={{ width: 12, height: 12, bgcolor: '#1976d2' }} />
                    <Typography variant="body2" color="text.secondary">
                      Actual
                    </Typography>
                  </Stack>
                  <Stack direction="row" spacing={1} alignItems="center">
                    <Box sx={{ width: 12, height: 12, bgcolor: '#2e7d32' }} />
                    <Typography variant="body2" color="text.secondary">
                      Optimized
                    </Typography>
                  </Stack>
                </Stack>
              </Stack>
            </Stack>
            {isGraphLoading ? (
              <Typography variant="caption" color="text.secondary">
                Loading graph data...
              </Typography>
            ) : null}
            {graphErrorMessage ? (
              <Typography variant="caption" color="error.main">
                {graphErrorMessage}
              </Typography>
            ) : null}
          </CardContent>
        </Card>

        <Stack spacing={1}>
          <Typography variant="h6" color="text.primary">
            Insights
          </Typography>
          <Stack direction={{ xs: 'column', md: 'row' }} spacing={2}>
            {insights.map((item) => (
              <Card key={item.title} elevation={0} sx={{ flex: 1, borderRadius: 3 }}>
                <CardContent>
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

        <Card elevation={0} sx={{ borderRadius: 3 }}>
          <CardContent sx={{ p: { xs: 2, md: 3 } }}>
            <Stack spacing={2.5}>
              <Stack spacing={0.5}>
                <Typography variant="h6" color="text.primary">
                  Smart Usage Timeline
                </Typography>
                <Typography variant="body2" color="text.secondary">
                  Drag to move • Resize edges • Snaps to hour on release
                </Typography>
              </Stack>

              <Stack direction="row" spacing={2} alignItems="flex-end">
                <Box sx={{ width: 72, flexShrink: 0 }} aria-hidden />
                <Box sx={{ flex: 1, minWidth: 0 }}>
                  <HourScale />
                </Box>
              </Stack>

              <Stack spacing={2}>
                <TimelineRangeBar label="EV" color="#1976d2" applianceId="ev" />
                <TimelineRangeBar label="AC" color="#ef6c00" applianceId="ac" />
                <TimelineRangeBar label="Laundry" color="#6a1b9a" applianceId="laundry" />
              </Stack>
            </Stack>
          </CardContent>
        </Card>

        <Card elevation={0} sx={{ borderRadius: 3 }}>
          <CardContent sx={{ p: { xs: 2, md: 3 } }}>
            <Stack spacing={2}>
              <Typography variant="h6" color="text.primary">
                🧠 Tell us your lifestyle
              </Typography>
              <Stack direction="row" spacing={1}>
                <Button
                  type="button"
                  variant={analysisInputMode === 'text' ? 'contained' : 'outlined'}
                  onClick={() => setAnalysisInputMode('text')}
                >
                  Analyze Text
                </Button>
                <Button
                  type="button"
                  variant={analysisInputMode === 'sliders' ? 'contained' : 'outlined'}
                  onClick={() => setAnalysisInputMode('sliders')}
                >
                  Analyze Sliders
                </Button>
              </Stack>

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
                  disabled={analysisInputMode !== 'text'}
                  fullWidth
                  multiline
                  minRows={2}
                  variant="standard"
                  placeholder='✨ Start typing... "Charge EV before 7 AM"'
                  InputProps={{ disableUnderline: true }}
                />
              </Box>

              <Stack direction="row" justifyContent="space-between" alignItems="center">
                <Typography variant="caption" color="text.secondary">
                  {analysisInputMode === 'text'
                    ? 'Analyzing only free text input'
                    : 'Analyzing only slider constraints'}
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
