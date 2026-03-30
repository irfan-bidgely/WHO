import Box from '@mui/material/Box';
import Typography from '@mui/material/Typography';
import { formatHourLabel } from '../utils/timeScale';

const HOURS = Array.from({ length: 24 }, (_, i) => i);

/**
 * Full 24-hour axis: one column per hour with tick marks, aligned with timeline tracks.
 */
export function HourScale() {
  return (
    <Box
      sx={{
        width: '100%',
        overflowX: 'auto',
        pb: 0.5,
        // Ensure all 24 hours stay usable on narrow viewports
        '&::-webkit-scrollbar': { height: 6 },
      }}
    >
      <Box sx={{ minWidth: { xs: 640, sm: '100%' } }}>
        {/* Hour labels */}
        <Box
          sx={{
            display: 'flex',
            width: '100%',
            justifyContent: 'stretch',
            alignItems: 'flex-end',
          }}
        >
          {HOURS.map((h) => (
            <Box
              key={h}
              sx={{
                flex: '1 1 0',
                minWidth: 0,
                textAlign: 'center',
                px: 0,
              }}
            >
              <Typography
                component="span"
                variant="caption"
                sx={{
                  fontSize: { xs: '0.65rem', sm: '0.7rem' },
                  color: 'text.secondary',
                  fontWeight: 500,
                  lineHeight: 1.1,
                  display: 'block',
                  whiteSpace: 'nowrap',
                }}
              >
                {formatHourLabel(h)}
              </Typography>
            </Box>
          ))}
        </Box>

        {/* Tick marks: one per hour; major emphasis every 6h */}
        <Box
          sx={{
            display: 'flex',
            width: '100%',
            height: 14,
            alignItems: 'flex-end',
            mt: 0.5,
          }}
        >
          {HOURS.map((h) => (
            <Box
              key={h}
              sx={{
                flex: '1 1 0',
                display: 'flex',
                justifyContent: 'center',
                minWidth: 0,
              }}
            >
              <Box
                sx={{
                  width: h % 6 === 0 ? 2 : 1,
                  height: h % 6 === 0 ? 12 : 7,
                  borderRadius: 0.5,
                  bgcolor: h % 6 === 0 ? 'text.disabled' : 'divider',
                }}
              />
            </Box>
          ))}
        </Box>

        {/* Baseline */}
        <Box
          sx={{
            height: 1,
            width: '100%',
            bgcolor: 'divider',
            mt: 0.25,
          }}
        />
      </Box>
    </Box>
  );
}
