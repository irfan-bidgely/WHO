import { createSlice, type PayloadAction } from '@reduxjs/toolkit';

export type WhoState = {
  /** Display label for the widget header */
  title: string;
};

const initialState: WhoState = {
  title: 'WHO',
};

export const whoSlice = createSlice({
  name: 'who',
  initialState,
  reducers: {
    setTitle: (state, action: PayloadAction<string>) => {
      state.title = action.payload;
    },
  },
});

export const { setTitle } = whoSlice.actions;
export default whoSlice.reducer;
