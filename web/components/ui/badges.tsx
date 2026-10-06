import { stageOf } from "@/lib/stages";
import { cn } from "@/lib/utils";

const GRADE_TONE: Record<string, string> = {
  "A+": "text-grade-a-plus border-grade-a-plus",
  A: "text-grade-a border-grade-a",
  B: "text-grade-b border-grade-b",
  C: "text-grade-c border-grade-c",
};

/** Setup grade and score; below C isn't a recommendation and shows "—". */
export function GradeBadge({
  grade,
  score,
  size = "sm",
}: {
  grade: string | null | undefined;
  score?: number | null;
  size?: "sm" | "lg";
}) {
  const tone = grade ? GRADE_TONE[grade] : undefined;
  return (
    <span
      role="img"
      className={cn(
        "tabular inline-flex items-baseline gap-1.5 rounded border px-1.5",
        size === "lg" ? "py-1 text-lg" : "py-px text-xs",
        tone ?? "border-border text-muted",
      )}
      aria-label={
        grade ? `Grade ${grade}${score != null ? `, score ${score.toFixed(0)}` : ""}` : "No grade"
      }
    >
      <span className="font-semibold">{grade ?? "—"}</span>
      {score != null && <span className="text-muted">{score.toFixed(0)}</span>}
    </span>
  );
}

export function StageBadge({
  state,
  className,
}: {
  state: string | null | undefined;
  className?: string;
}) {
  const stage = stageOf(state);
  if (!stage) return <span className="text-muted">—</span>;
  return (
    <span className={cn("inline-flex items-center gap-1 whitespace-nowrap", stage.tone, className)}>
      <span aria-hidden>{stage.icon}</span>
      {stage.label}
    </span>
  );
}

/** A signed change with ▲/▼ so it never relies on colour: ▲ +1.35%, ▼ −0.40%. */
export function Change({
  value,
  suffix = "%",
  prefix = "",
  digits = 2,
  className,
}: {
  value: number | null | undefined;
  suffix?: string;
  /** After the sign: "$" gives ▲ +$600. */
  prefix?: string;
  digits?: number;
  className?: string;
}) {
  if (value == null) return <span className={cn("text-muted", className)}>—</span>;
  const shown = Math.abs(value).toLocaleString("en-US", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
  const zero = Number(Math.abs(value).toFixed(digits)) === 0;
  const up = value > 0 && !zero;
  const down = value < 0 && !zero;
  return (
    <span
      className={cn("tabular whitespace-nowrap", up && "text-rise", down && "text-fall", className)}
    >
      <span aria-hidden>{up ? "▲" : down ? "▼" : "–"}</span> {up ? "+" : down ? "−" : ""}
      {prefix}
      {shown}
      {suffix}
    </span>
  );
}
