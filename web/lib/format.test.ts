import { describe, expect, it } from "vitest";
import { formatDuration, formatPercent, humanize, safeNext } from "./format";

describe("format helpers", () => {
  it("only allows same-site redirects after sign-in", () => {
    expect(safeNext("/admin/data?tab=1")).toBe("/admin/data?tab=1");
    expect(safeNext(null)).toBe("/");
    expect(safeNext("//evil.example")).toBe("/");
    expect(safeNext("/\\evil.example")).toBe("/");
    expect(safeNext("https://evil.example")).toBe("/");
  });

  it("formats durations, percentages and keys", () => {
    expect(formatDuration(null)).toBe("—");
    expect(formatDuration(42_000)).toBe("42s");
    expect(formatDuration(3_725_000)).toBe("1h 2m");
    expect(formatPercent(1, 3)).toBe("33%");
    expect(formatPercent(5, 0)).toBe("0%");
    expect(humanize("missing_sessions")).toBe("Missing sessions");
  });
});
