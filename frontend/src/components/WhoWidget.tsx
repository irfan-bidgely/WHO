import { useId } from 'react';
import Box from '@mui/material/Box';
import Button from '@mui/material/Button';
import Paper from '@mui/material/Paper';
import Stack from '@mui/material/Stack';
import Typography from '@mui/material/Typography';
import { setTitle } from '../features/who/whoSlice';
import { useAppDispatch, useAppSelector } from '../store/hooks';

/**
 * Primary SPA surface: WHO content and state wired through Redux Toolkit.
 */
export function WhoWidget() {
  const titleId = useId();
  const dispatch = useAppDispatch();
  const title = useAppSelector((s) => s.who.title);

  return (
    <Paper
      component="section"
      elevation={2}
      sx={{ p: 3, maxWidth: 480, mx: 'auto', mt: 4 }}
      aria-labelledby={titleId}
    >
      <Stack spacing={2} alignItems="flex-start">
        <Typography id={titleId} variant="h5" component="h1">
          {title}
        </Typography>
        <Typography variant="body2" color="text.secondary">
          Single-page shell: extend this widget and connect new slices as the
          app grows.
        </Typography>
        <Box>
          <Button
            type="button"
            variant="contained"
            size="small"
            onClick={() => dispatch(setTitle(title === 'WHO' ? 'WHO Widget' : 'WHO'))}
          >
            Toggle title
          </Button>
        </Box>
      </Stack>
    </Paper>
  );
}
