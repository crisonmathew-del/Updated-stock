import type { ReactNode } from "react";
import type { Severity } from "@/lib/api";
import { cn } from "@/lib/utils";

export function Panel({
  title,
  description,
  actions,
  children,
}: {
  title: string;
  description?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
}) {
  const id = `panel-${title.toLowerCase().replace(/\W+/g, "-")}`;
  return (
    <section
      aria-labelledby={id}
      className="flex flex-col gap-4 rounded-md border border-border p-5"
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex flex-col gap-1">
          <h2 id={id} className="font-medium">
            {title}
          </h2>
          {description && <p className="text-sm text-muted">{description}</p>}
        </div>
        {actions && <div className="flex flex-wrap gap-2">{actions}</div>}
      </div>
      {children}
    </section>
  );
}

export function Stat({ label, value, hint }: { label: string; value: ReactNode; hint?: string }) {
  return (
    <div className="flex flex-col gap-0.5">
      <dt className="text-xs text-muted">{label}</dt>
      <dd className="tabular text-lg font-medium">{value}</dd>
      {hint && <dd className="text-xs text-muted">{hint}</dd>}
    </div>
  );
}

const SEVERITY: Record<Severity, { symbol: string; label: string; className: string }> = {
  critical: { symbol: "✕", label: "Critical", className: "text-fail" },
  warning: { symbol: "!", label: "Warning", className: "text-warn" },
  info: { symbol: "i", label: "Info", className: "text-muted" },
};

export function SeverityBadge({ severity, count }: { severity: Severity; count?: number }) {
  const s = SEVERITY[severity];
  return (
    <span className={cn("inline-flex items-center gap-1.5 text-sm", s.className)}>
      <span aria-hidden className="w-3 text-center font-semibold">
        {s.symbol}
      </span>
      {count !== undefined ? (
        <span className="tabular">
          {count} {s.label.toLowerCase()}
        </span>
      ) : (
        s.label
      )}
    </span>
  );
}

export function ActionButton({
  onClick,
  disabled,
  children,
}: {
  onClick: () => void;
  disabled?: boolean;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className="rounded-md border border-border px-3 py-1.5 text-sm hover:bg-foreground/5 disabled:opacity-50"
    >
      {children}
    </button>
  );
}
