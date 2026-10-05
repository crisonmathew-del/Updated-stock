import type { SetupState } from "@/lib/api";

/**
 * Lifecycle stages as the UI shows them. Only the stages that call for action get a colour
 * (near pivot: Tide, breakout: Rise, failed: Fall); the rest are neutral. Every stage always
 * shows its icon and label, so colour is never the only cue.
 */
export const STAGES: Record<SetupState, { label: string; icon: string; tone: string }> = {
  watch: { label: "Watch", icon: "◌", tone: "text-muted" },
  basing: { label: "Basing", icon: "▭", tone: "text-foreground" },
  near_pivot: { label: "Near pivot", icon: "◎", tone: "text-tide-ink" },
  breakout: { label: "Breakout", icon: "▲", tone: "text-rise" },
  extended: { label: "Extended", icon: "⤒", tone: "text-muted" },
  failed: { label: "Failed", icon: "✕", tone: "text-fall" },
  invalidated: { label: "Invalidated", icon: "⊘", tone: "text-muted" },
};

export const BOARD_ORDER: SetupState[] = ["basing", "near_pivot", "breakout", "extended", "failed"];

export function stageOf(state: string | null | undefined) {
  return state && state in STAGES ? STAGES[state as SetupState] : null;
}

const PATTERN_LABELS: Record<string, string> = {
  vcp: "VCP",
  cup_with_handle: "Cup with handle",
  flat_base: "Flat base",
  high_tight_flag: "High tight flag",
  three_weeks_tight: "3 weeks tight",
  ascending_base: "Ascending base",
  pocket_pivot: "Pocket pivot",
  earnings_gap: "Earnings gap",
};

export function patternLabel(type: string | null | undefined): string {
  if (!type) return "—";
  return PATTERN_LABELS[type] ?? type;
}
