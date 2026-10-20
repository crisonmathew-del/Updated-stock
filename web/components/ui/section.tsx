import type { ReactNode } from "react";
import { InfoTip } from "@/components/ui/info-tip";
import type { GlossaryKey } from "@/lib/glossary";
import { cn } from "@/lib/utils";

/** A panel (stock page, dashboard): a quiet card with a small heading, an optional "?" that
 * explains it in plain English, and an optional right-hand note. */
export function Section({
  title,
  note,
  children,
  className,
  anchor,
  help,
}: {
  title: string;
  note?: ReactNode;
  children: ReactNode;
  className?: string;
  /** An id for links to this panel (e.g. "/#market"). */
  anchor?: string;
  /** The glossary term the "?" explains. */
  help?: GlossaryKey;
}) {
  const id = `section-${title.toLowerCase().replace(/\W+/g, "-")}`;
  return (
    <section
      id={anchor}
      aria-labelledby={id}
      className={cn(
        "flex flex-col gap-3 rounded-lg border border-border bg-surface p-4",
        className,
      )}
    >
      <div className="flex items-baseline justify-between gap-2">
        {/* The "?" sits beside the heading, not in it, so the panel's name stays the title. */}
        <div className="flex items-center gap-1.5">
          <h2 id={id} className="text-xs font-semibold tracking-wide text-muted uppercase">
            {title}
          </h2>
          {help && <InfoTip term={help} />}
        </div>
        {note && <span className="text-xs text-muted">{note}</span>}
      </div>
      {children}
    </section>
  );
}

/** ✓ / ~ / ✕ / – with colour, so status never depends on colour alone. */
export function StatusMark({
  status,
}: {
  status: "pass" | "partial" | "fail" | "no_data" | boolean;
}) {
  const s = status === true ? "pass" : status === false ? "fail" : status;
  const look = {
    pass: ["✓", "text-rise", "passes"],
    partial: ["~", "text-warn", "partial"],
    fail: ["✕", "text-fall", "fails"],
    no_data: ["–", "text-muted", "no data"],
  }[s];
  return (
    <span
      role="img"
      className={cn("inline-block w-4 shrink-0 text-center", look[1])}
      aria-label={look[2]}
    >
      {look[0]}
    </span>
  );
}
