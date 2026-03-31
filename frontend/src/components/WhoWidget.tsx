import { useId, useMemo, useState } from 'react';
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
import { applyConstraintWindow, type TimelineApplianceId } from '../features/who/whoSlice';
import { useAppDispatch, useAppSelector } from '../store/hooks';
import { HourScale } from './HourScale';
import { TimelineRangeBar } from './TimelineRangeBar';

/**
 * Primary SPA surface: WHO content and state wired through Redux Toolkit.
 */
export function WhoWidget() {
  const titleId = useId();
  const dispatch = useAppDispatch();
  const title = useAppSelector((s) => s.who.title);
  const [lifestyleInput, setLifestyleInput] = useState('');
  const [isAnalyzing, setIsAnalyzing] = useState(false);
  const [analysisSuccessMessage, setAnalysisSuccessMessage] = useState<string | null>(null);
  const [analysisErrorMessage, setAnalysisErrorMessage] = useState<string | null>(null);
  const applianceIdToTimelineId: Record<number, TimelineApplianceId | undefined> = {
    18: 'ev',
    4: 'ac',
  };

  const handleAnalyzeConstraints = async () => {
    if (!lifestyleInput.trim()) {
      setAnalysisSuccessMessage(null);
      setAnalysisErrorMessage('Please enter a constraint first.');
      return;
    }
    setIsAnalyzing(true);
    setAnalysisSuccessMessage(null);
    setAnalysisErrorMessage(null);
    try {
      const result = await analyzeConstraint({
        constraintText: lifestyleInput.trim(),
      });

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
        setAnalysisErrorMessage('No allowed window found in the text.');
      } else {
        setAnalysisSuccessMessage(`Applied constraints to ${appliedCount} appliance(s).`);
      }
    } catch {
      setAnalysisErrorMessage('Could not analyze constraints. Please try again.');
    } finally {
      setIsAnalyzing(false);
    }
  };

  const chartData = useMemo(
    () => [
      { label: 'AC', actual: 100, optimized: 70 },
      { label: 'EV', actual: 120, optimized: 80 },
      { label: 'Laundry', actual: 60, optimized: 40 },
    ],
    [],
  );

  const insights = useMemo(
    () => [
      { title: '⚡ EV Optimization', value: 'Save ₹70/day', color: '#2e7d32' },
      { title: '❄️ AC Usage', value: 'Peak overlap', color: '#ef6c00' },
      { title: '🧺 Laundry', value: 'Easy to shift', color: '#6a1b9a' },
    ],
    [],
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
                  minRows={2}
                  variant="standard"
                  placeholder='✨ Start typing... "Charge EV before 7 AM"'
                  InputProps={{ disableUnderline: true }}
                />
              </Box>

              <Stack direction="row" justifyContent="space-between" alignItems="center">
                <Typography variant="caption" color="text.secondary">
                  Understands natural language • No rules needed
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
