import { create } from "zustand";

export type Toast = {
  id: number;
  text: string;
  tone: "info" | "error" | "alert";
  /** Where "Open" goes (an alert's stock page). */
  href?: string;
};

type ToastState = {
  toasts: Toast[];
  push: (text: string, tone?: Toast["tone"], options?: { href?: string; ms?: number }) => void;
  dismiss: (id: number) => void;
};

let next = 1;
const MAX_TOASTS = 4;

export const useToasts = create<ToastState>((set) => ({
  toasts: [],
  push: (text, tone = "info", options = {}) => {
    const id = next++;
    set((s) => ({
      toasts: [...s.toasts, { id, text, tone, href: options.href }].slice(-MAX_TOASTS),
    }));
    setTimeout(
      () => set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) })),
      options.ms ?? 4000,
    );
  },
  dismiss: (id) => set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) })),
}));
