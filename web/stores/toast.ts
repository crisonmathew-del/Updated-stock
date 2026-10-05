import { create } from "zustand";

export type Toast = { id: number; text: string; tone: "info" | "error" };

type ToastState = {
  toasts: Toast[];
  push: (text: string, tone?: Toast["tone"]) => void;
  dismiss: (id: number) => void;
};

let next = 1;

export const useToasts = create<ToastState>((set) => ({
  toasts: [],
  push: (text, tone = "info") => {
    const id = next++;
    set((s) => ({ toasts: [...s.toasts, { id, text, tone }] }));
    setTimeout(() => set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) })), 4000);
  },
  dismiss: (id) => set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) })),
}));
