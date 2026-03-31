import { createSlice, type PayloadAction } from '@reduxjs/toolkit';

/** 0–100 maps across the day (left = 12 AM, right = next 12 AM). */
export type TimelineRange = {
  startPct: number;
  endPct: number;
};

export type AllowedWindow = {
  startHour: number;
  endHour: number;
};

export type BlockConstraints = {
  maxShiftHours: number | null;
  allowedWindows: AllowedWindow[] | null;
};

export type WhoState = {
  /** Display label for the widget header */
  title: string;
  /** Smart Usage Timeline: one range per appliance `appId` (same set as the cost graph). */
  timeline: Record<number, TimelineRange>;
};

const MIN_RANGE_PCT = 2;

const clampRange = (startPct: number, endPct: number): TimelineRange => {
  let a = Math.min(startPct, endPct);
  let b = Math.max(startPct, endPct);
  if (b - a < MIN_RANGE_PCT) {
    const mid = (a + b) / 2;
    a = mid - MIN_RANGE_PCT / 2;
    b = mid + MIN_RANGE_PCT / 2;
  }
  a = Math.max(0, a);
  b = Math.min(100, b);
  if (b - a < MIN_RANGE_PCT) {
    if (a === 0) b = MIN_RANGE_PCT;
    else a = b - MIN_RANGE_PCT;
  }
  return { startPct: a, endPct: b };
};

/** Default window when an appliance first appears (spread by appId so rows differ). */
export function defaultTimelineRangeForAppId(appId: number): TimelineRange {
  const h = (appId * 7) % 20;
  const startPct = (h / 24) * 100;
  const endPct = Math.min(100, ((h + 6) / 24) * 100);
  return clampRange(startPct, endPct);
}

const initialState: WhoState = {
  title: 'Whole Home Optimizer',
  timeline: {},
};

export const whoSlice = createSlice({
  name: 'who',
  initialState,
  reducers: {
    setTitle: (state, action: PayloadAction<string>) => {
      state.title = action.payload;
    },
    setTimelineRange: (
      state,
      action: PayloadAction<{
        id: number;
        startPct: number;
        endPct: number;
      }>,
    ) => {
      const { id, startPct, endPct } = action.payload;
      state.timeline[id] = clampRange(startPct, endPct);
    },
    applyConstraintWindow: (
      state,
      action: PayloadAction<{
        id: number;
        window: AllowedWindow;
      }>,
    ) => {
      const { id, window } = action.payload;
      const startPct = (window.startHour / 24) * 100;
      const endPct = (window.endHour / 24) * 100;
      state.timeline[id] = clampRange(startPct, endPct);
    },
    /**
     * Keep slider rows aligned with graph appliances: add missing `appId`s, drop ones not in the graph.
     */
    syncTimelineForAppIds: (state, action: PayloadAction<number[]>) => {
      const want = new Set(action.payload);
      for (const key of Object.keys(state.timeline)) {
        const id = Number(key);
        if (!want.has(id)) {
          delete state.timeline[id];
        }
      }
      for (const id of action.payload) {
        if (state.timeline[id] == null) {
          state.timeline[id] = defaultTimelineRangeForAppId(id);
        }
      }
    },
  },
});

export const { setTitle, setTimelineRange, applyConstraintWindow, syncTimelineForAppIds } =
  whoSlice.actions;
export default whoSlice.reducer;
