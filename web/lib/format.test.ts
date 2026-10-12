import { describe, expect, it, vi } from "vitest";
import {
  easternTime,
  easternToLocalHHMM,
  formatClock,
  formatDuration,
  formatEasternSchedule,
  formatMarketTime,
  formatPercent,
  humanize,
  localToEasternHHMM,
  localZoneName,
  safeNext,
} from "./format";

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

describe("times in the viewer's zone (the tests run as a viewer in London)", () => {
  it("shows a market event on the viewer's clock with the zone's name", () => {
    // 10:15:30 in New York in October is 15:15:30 British Summer Time.
    expect(formatMarketTime("2026-10-05T14:15:30Z")).toBe("15:15:30 BST");
    expect(formatClock("2026-10-05T14:15:30Z", { seconds: false })).toBe("15:15 BST");
    expect(formatClock("2026-10-05T14:15:30Z", { seconds: false, zone: false })).toBe("15:15");
    // After the UK clocks go back: Greenwich Mean Time.
    expect(formatMarketTime("2026-11-16T14:15:30Z")).toBe("14:15:30 GMT");
    expect(formatMarketTime(null)).toBe("—");
  });

  it("finds the instant of a US/Eastern schedule time, either side of daylight saving", () => {
    const monday = new Date("2026-10-12T12:00:00Z");
    expect(easternTime("08:00", undefined, monday).toISOString()).toBe("2026-10-12T12:00:00.000Z");
    // The next Sunday 18:00 in New York, still on summer time (EDT, UTC−4).
    expect(easternTime("18:00", 0, monday).toISOString()).toBe("2026-10-18T22:00:00.000Z");
    // In winter New York is UTC−5.
    const winter = new Date("2026-12-07T12:00:00Z");
    expect(easternTime("17:30", undefined, winter).toISOString()).toBe("2026-12-07T22:30:00.000Z");
  });

  it("converts the alert settings' US/Eastern times to and from the viewer's clock", () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    try {
      vi.setSystemTime(new Date("2026-10-12T12:00:00Z")); // both on summer time: 5 hours apart
      expect(easternToLocalHHMM("17:30")).toBe("22:30");
      expect(localToEasternHHMM("22:30")).toBe("17:30");
      expect(localToEasternHHMM("07:00")).toBe("02:00");
      expect(localZoneName()).toBe("BST");
      expect(formatEasternSchedule("18:00", 0)).toBe("Sunday 23:00 BST");
      // The week the UK is back on GMT but New York still on summer time: 4 hours apart.
      vi.setSystemTime(new Date("2026-10-27T12:00:00Z"));
      expect(easternToLocalHHMM("17:30")).toBe("21:30");
      expect(localToEasternHHMM("21:30")).toBe("17:30");
    } finally {
      vi.useRealTimers();
    }
  });
});
