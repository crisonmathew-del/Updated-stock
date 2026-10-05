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

describe("market formatters", () => {
  it("formats prices, signed changes and rank moves", async () => {
    const { formatPrice, formatChange, formatRankChange } = await import("./format");
    expect(formatPrice(1234.5)).toBe("1,234.50");
    expect(formatPrice(null)).toBe("—");
    expect(formatChange(0.125)).toBe("+12.5%");
    expect(formatChange(-0.04)).toBe("−4.0%");
    expect(formatRankChange(3)).toBe("▲ 3");
    expect(formatRankChange(-2)).toBe("▼ 2");
    expect(formatRankChange(0)).toBe("– 0");
  });
});

describe("setup formatters", () => {
  it("formats percent points, R multiples and the distance to the pivot", async () => {
    const { formatPctPoints, formatR, formatReadiness } = await import("./format");
    expect(formatPctPoints(1.37)).toBe("+1.4%");
    expect(formatPctPoints(-2.9)).toBe("−2.9%");
    expect(formatPctPoints(-0.04)).toBe("0.0%"); // never "−0.0%"
    expect(formatPctPoints(null)).toBe("—");
    expect(formatR(0.37)).toBe("+0.37R");
    expect(formatR(-1)).toBe("−1.00R");
    expect(formatR(null)).toBe("");
    expect(formatReadiness(2.04)).toBe("2.0% below");
    expect(formatReadiness(-4.5)).toBe("4.5% above");
    expect(formatReadiness(null)).toBe("—");
  });
});
