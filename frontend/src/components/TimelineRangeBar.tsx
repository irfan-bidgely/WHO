import { useCallback, useEffect, useRef, type PointerEvent as ReactPointerEvent } from 'react';
import Box from '@mui/material/Box';
import Stack from '@mui/material/Stack';
import Tooltip from '@mui/material/Tooltip';
import Typography from '@mui/material/Typography';
import { alpha } from '@mui/material/styles';
import { useStore } from 'react-redux';
import {
  setTimelineRange,
  type TimelineApplianceId,
} from '../features/who/whoSlice';
import type { RootState } from '../store/store';
import { useAppDispatch, useAppSelector } from '../store/hooks';
import { formatPctRangeLabel, snapPctToHour } from '../utils/timeScale';

const HANDLE_PX = 14;
const MIN_RANGE_PCT = 2;

type DragMode = 'move' | 'resize-left' | 'resize-right';

type DragState = {
  mode: DragMode;
  pointerId: number;
  startPct0: number;
  endPct0: number;
  originClientX: number;
  trackWidthPx: number;
};

type Props = {
  label: string;
  color: string;
  applianceId: TimelineApplianceId;
};

export function TimelineRangeBar({ label, color, applianceId }: Props) {
  const dispatch = useAppDispatch();
  const store = useStore<RootState>();
  const { startPct, endPct } = useAppSelector((s) => s.who.timeline[applianceId]);
  const trackRef = useRef<HTMLDivElement>(null);
  const dragRef = useRef<DragState | null>(null);

  const dispatchRange = useCallback(
    (nextStart: number, nextEnd: number) => {
      dispatch(setTimelineRange({ id: applianceId, startPct: nextStart, endPct: nextEnd }));
    },
    [applianceId, dispatch],
  );

  const snapCurrentRangeToHour = useCallback(() => {
    const { startPct: s, endPct: e } = store.getState().who.timeline[applianceId];
    dispatch(
      setTimelineRange({
        id: applianceId,
        startPct: snapPctToHour(s),
        endPct: snapPctToHour(e),
      }),
    );
  }, [applianceId, dispatch, store]);

  const clientToPct = useCallback((clientX: number) => {
    const el = trackRef.current;
    if (!el) return 0;
    const r = el.getBoundingClientRect();
    if (r.width <= 0) return 0;
    const p = ((clientX - r.left) / r.width) * 100;
    return Math.min(100, Math.max(0, p));
  }, []);

  useEffect(() => {
    const onWinPointerUp = (e: PointerEvent) => {
      const drag = dragRef.current;
      if (drag && e.pointerId === drag.pointerId) {
        snapCurrentRangeToHour();
      }
      dragRef.current = null;
    };
    window.addEventListener('pointerup', onWinPointerUp);
    window.addEventListener('pointercancel', onWinPointerUp);
    return () => {
      window.removeEventListener('pointerup', onWinPointerUp);
      window.removeEventListener('pointercancel', onWinPointerUp);
    };
  }, [snapCurrentRangeToHour]);

  const onPointerMove = useCallback(
    (e: PointerEvent) => {
      const drag = dragRef.current;
      const track = trackRef.current;
      if (!drag || !track) return;

      if (e.pointerId !== drag.pointerId) return;

      const widthPx = drag.trackWidthPx || track.getBoundingClientRect().width;
      const dxPct = ((e.clientX - drag.originClientX) / widthPx) * 100;

      if (drag.mode === 'move') {
        const span = drag.endPct0 - drag.startPct0;
        let ns = drag.startPct0 + dxPct;
        let ne = drag.endPct0 + dxPct;
        if (ns < 0) {
          ns = 0;
          ne = span;
        }
        if (ne > 100) {
          ne = 100;
          ns = 100 - span;
        }
        dispatchRange(ns, ne);
      } else if (drag.mode === 'resize-left') {
        const x = clientToPct(e.clientX);
        const right = drag.endPct0;
        let left = Math.min(x, right - MIN_RANGE_PCT);
        left = Math.max(0, left);
        dispatchRange(left, right);
      } else {
        const x = clientToPct(e.clientX);
        const left = drag.startPct0;
        let right = Math.max(x, left + MIN_RANGE_PCT);
        right = Math.min(100, right);
        dispatchRange(left, right);
      }
    },
    [clientToPct, dispatchRange],
  );

  useEffect(() => {
    window.addEventListener('pointermove', onPointerMove);
    return () => window.removeEventListener('pointermove', onPointerMove);
  }, [onPointerMove]);

  const hitTest = useCallback(
    (clientX: number): DragMode | null => {
      const track = trackRef.current;
      if (!track) return null;
      const r = track.getBoundingClientRect();
      const widthPx = r.width;
      const barLeftPx = r.left + (startPct / 100) * widthPx;
      const barRightPx = r.left + (endPct / 100) * widthPx;
      if (clientX < barLeftPx || clientX > barRightPx) return null;
      if (clientX <= barLeftPx + HANDLE_PX) return 'resize-left';
      if (clientX >= barRightPx - HANDLE_PX) return 'resize-right';
      return 'move';
    },
    [startPct, endPct],
  );

  const onTrackPointerDown = (e: ReactPointerEvent<HTMLDivElement>) => {
    if (e.button !== 0) return;
    const track = trackRef.current;
    if (!track) return;

    const mode = hitTest(e.clientX);
    if (mode === null) return;
    const r = track.getBoundingClientRect();

    e.preventDefault();
    (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);

    dragRef.current = {
      mode,
      pointerId: e.pointerId,
      startPct0: startPct,
      endPct0: endPct,
      originClientX: e.clientX,
      trackWidthPx: r.width,
    };
  };

  const onHandlePointerDown =
    (mode: DragMode) => (e: ReactPointerEvent<HTMLDivElement>) => {
      if (e.button !== 0) return;
      const track = trackRef.current;
      if (!track) return;
      const r = track.getBoundingClientRect();
      e.stopPropagation();
      e.preventDefault();
      (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
      dragRef.current = {
        mode,
        pointerId: e.pointerId,
        startPct0: startPct,
        endPct0: endPct,
        originClientX: e.clientX,
        trackWidthPx: r.width,
      };
    };

  const rangeLabel = formatPctRangeLabel(startPct, endPct);

  return (
    <Stack direction="row" spacing={2} alignItems="center">
      <Typography sx={{ width: 72, flexShrink: 0 }} variant="body2" color="text.secondary">
        {label}
      </Typography>
      <Box sx={{ flex: 1, minWidth: 0 }}>
        <Tooltip title={rangeLabel} placement="top" enterDelay={200}>
          <Box
          ref={trackRef}
          onPointerDown={onTrackPointerDown}
          sx={{
            width: '100%',
            position: 'relative',
            height: 44,
            touchAction: 'none',
            userSelect: 'none',
            cursor: 'grab',
            '&:active': { cursor: 'grabbing' },
            borderRadius: 2,
            bgcolor: alpha('#ffffff', 0.45),
            backdropFilter: 'blur(14px)',
            WebkitBackdropFilter: 'blur(14px)',
            border: `1px solid ${alpha('#ffffff', 0.65)}`,
            boxShadow: 'inset 0 1px 2px rgba(0,0,0,0.06)',
            overflow: 'hidden',
          }}
          role="slider"
          aria-label={`${label} usage from ${rangeLabel}`}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={Math.round(((startPct + endPct) / 2) * 10) / 10}
        >
          {/* Hour guides (subtle): 24 vertical lines */}
          <Box
            aria-hidden
            sx={{
              position: 'absolute',
              inset: 0,
              opacity: 0.35,
              backgroundImage: `repeating-linear-gradient(
                90deg,
                ${alpha('#000', 0.06)} 0,
                ${alpha('#000', 0.06)} 1px,
                transparent 1px,
                transparent calc(100% / 24)
              )`,
              pointerEvents: 'none',
            }}
          />

          <Box
            sx={{
              position: 'absolute',
              left: `${startPct}%`,
              width: `${endPct - startPct}%`,
              top: '50%',
              transform: 'translateY(-50%)',
              height: 30,
              borderRadius: 2,
              pointerEvents: 'none',
              background: `linear-gradient(155deg, ${alpha(color, 0.72)}, ${alpha(color, 0.38)})`,
              backdropFilter: 'blur(12px)',
              WebkitBackdropFilter: 'blur(12px)',
              border: `1px solid ${alpha('#ffffff', 0.55)}`,
              boxShadow: `0 4px 16px ${alpha(color, 0.35)}, inset 0 1px 0 ${alpha('#ffffff', 0.35)}`,
            }}
          />

          {/* Left handle */}
          <Box
            onPointerDown={onHandlePointerDown('resize-left')}
            sx={{
              position: 'absolute',
              left: `${startPct}%`,
              top: '50%',
              transform: 'translate(-50%, -50%)',
              width: 18,
              height: 32,
              cursor: 'ew-resize',
              zIndex: 2,
              touchAction: 'none',
            }}
            aria-label={`${label} start time`}
          >
            <Box
              sx={{
                position: 'absolute',
                left: '50%',
                top: '50%',
                transform: 'translate(-50%, -50%)',
                width: 12,
                height: 12,
                borderRadius: '50%',
                bgcolor: alpha('#ffffff', 0.95),
                border: `1px solid ${alpha(color, 0.5)}`,
                boxShadow: '0 2px 6px rgba(0,0,0,0.15)',
                pointerEvents: 'none',
              }}
            />
          </Box>

          {/* Right handle */}
          <Box
            onPointerDown={onHandlePointerDown('resize-right')}
            sx={{
              position: 'absolute',
              left: `${endPct}%`,
              top: '50%',
              transform: 'translate(-50%, -50%)',
              width: 18,
              height: 32,
              cursor: 'ew-resize',
              zIndex: 2,
              touchAction: 'none',
            }}
            aria-label={`${label} end time`}
          >
            <Box
              sx={{
                position: 'absolute',
                left: '50%',
                top: '50%',
                transform: 'translate(-50%, -50%)',
                width: 12,
                height: 12,
                borderRadius: '50%',
                bgcolor: alpha('#ffffff', 0.95),
                border: `1px solid ${alpha(color, 0.5)}`,
                boxShadow: '0 2px 6px rgba(0,0,0,0.15)',
                pointerEvents: 'none',
              }}
            />
          </Box>
        </Box>
        </Tooltip>
      </Box>
    </Stack>
  );
}
