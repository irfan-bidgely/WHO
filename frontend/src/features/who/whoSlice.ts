import { createSlice, type PayloadAction } from '@reduxjs/toolkit';

/** 0–100 maps across the day (left = 12 AM, right = next 12 AM). */
export type TimelineApplianceId = 'ev' | 'ac' | 'laundry';

export type TimelineRange = {
  startPct: number;
  endPct: number;
};

export type WhoState = {
  /** Display label for the widget header */
  title: string;
  /** Smart Usage Timeline: draggable / resizable ranges per appliance */
  timeline: Record<TimelineApplianceId, TimelineRange>;
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

const initialState: WhoState = {
  title: 'Whole Home Optimizer',
  timeline: {
    ev: { startPct: (4 / 24) * 100, endPct: (10 / 24) * 100 },
    ac: { startPct: (17 / 24) * 100, endPct: (22 / 24) * 100 },
    laundry: { startPct: (20 / 24) * 100, endPct: (22 / 24) * 100 },
  },
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
        id: TimelineApplianceId;
        startPct: number;
        endPct: number;
      }>,
    ) => {
      const { id, startPct, endPct } = action.payload;
      state.timeline[id] = clampRange(startPct, endPct);
    },
  },
});

export const { setTitle, setTimelineRange } = whoSlice.actions;
export default whoSlice.reducer;
