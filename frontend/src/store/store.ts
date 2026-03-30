import { configureStore } from '@reduxjs/toolkit';
import whoReducer from '../features/who/whoSlice';

export const store = configureStore({
  reducer: {
    who: whoReducer,
  },
});

export type RootState = ReturnType<typeof store.getState>;
export type AppDispatch = typeof store.dispatch;
