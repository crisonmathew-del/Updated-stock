"use client";

import { useSyncExternalStore } from "react";
import { currentTheme, subscribeTheme, type Theme } from "@/lib/theme";

/** The current theme; re-renders when the user switches it. "dark" during server rendering. */
export function useTheme(): Theme {
  return useSyncExternalStore(subscribeTheme, currentTheme, () => "dark");
}
