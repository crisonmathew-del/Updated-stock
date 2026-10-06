"use client";

import { useToasts } from "@/stores/toast";
import { cn } from "@/lib/utils";

/** Short confirmations ("Added SPOT to Watchlist") and errors, bottom right, announced politely. */
export function Toaster() {
  const { toasts, dismiss } = useToasts();
  return (
    <div
      aria-live="polite"
      className="pointer-events-none fixed right-4 bottom-20 z-50 flex flex-col items-end gap-2 sm:bottom-4"
    >
      {toasts.map((t) => (
        <div
          key={t.id}
          className={cn(
            "pointer-events-auto flex items-center gap-3 rounded-md border bg-surface px-3 py-2 text-sm shadow-lg",
            t.tone === "error" ? "border-fall" : "border-border",
          )}
        >
          <span>
            {t.tone === "error" && (
              <span aria-hidden className="text-fall">
                ✕{" "}
              </span>
            )}
            {t.text}
          </span>
          <button
            type="button"
            onClick={() => dismiss(t.id)}
            className="text-muted hover:text-foreground"
            aria-label="Dismiss"
          >
            ×
          </button>
        </div>
      ))}
    </div>
  );
}
