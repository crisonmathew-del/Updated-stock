import { describe, expect, it } from "vitest";
import type { ScreenerSnapshot, ScreenFilter } from "@/lib/api";
import {
  applyFilters,
  decode,
  describeFilter,
  display,
  FIELDS,
  matches,
  newFilter,
  PRESETS,
  type Row,
  sameScreen,
  sortValue,
  sparkPoints,
  toCsv,
} from "./screener";

const FIELD_NAMES = Object.keys(FIELDS);

function row(values: Partial<Row>): Row {
  const base = Object.fromEntries(FIELD_NAMES.map((f) => [f, null])) as unknown as Row;
  return { ...base, symbol: "X", name: "X Corp", spark: "", ...values };
}

const SPOT = row({
  symbol: "SPOT",
  name: "Spotify Technology S.A.",
  close: 94,
  rs_rating: 92,
  tt_pass: true,
  tt_passed: 8,
  liquid: true,
  pattern: "vcp",
  setup_state: "near_pivot",
  grade: "A",
  score: 83.5,
  readiness_pct: 1.04,
  fund_grade: "A",
  group_rank: 4,
});
const SLOW = row({ symbol: "SLOW", close: 12.5, rs_rating: 40, liquid: true, fund_grade: "C" });
const THIN = row({ symbol: "THIN", close: 4, rs_rating: 95, liquid: false });

describe("decode", () => {
  it("turns the columnar snapshot into one object per stock", () => {
    const snapshot: ScreenerSnapshot = {
      as_of: "2026-10-02",
      fields: ["symbol", "close", "tt_pass"],
      rows: [
        ["SPOT", 94, true],
        ["AAPL", 230.5, false],
      ],
      groups: 0,
    };
    expect(decode(snapshot)).toEqual([
      { symbol: "SPOT", close: 94, tt_pass: true },
      { symbol: "AAPL", close: 230.5, tt_pass: false },
    ]);
  });

  it("covers every field the API sends", () => {
    // Mirrors api/app/api/routes/screener.py FIELDS (the API test checks that list).
    expect(FIELD_NAMES).toHaveLength(34);
  });
});

describe("filters", () => {
  it("applies ranges inclusively and fails missing numbers", () => {
    const rs85 = { field: "rs_rating", op: "between", min: 85, max: null } as const;
    expect(matches(SPOT, [rs85])).toBe(true);
    expect(matches(SLOW, [rs85])).toBe(false);
    expect(matches(row({ rs_rating: 85 }), [rs85])).toBe(true);
    expect(matches(row({ rs_rating: null }), [rs85])).toBe(false);
    const price = { field: "close", op: "between", min: 10, max: 94 } as const;
    expect(applyFilters([SPOT, SLOW, THIN], [price]).map((r) => r.symbol)).toEqual([
      "SPOT",
      "SLOW",
    ]);
  });

  it("ignores filters with nothing chosen yet", () => {
    expect(matches(SLOW, [newFilter("rs_rating"), newFilter("pattern")])).toBe(true);
  });

  it("matches yes/no and lists", () => {
    expect(matches(SPOT, [{ field: "tt_pass", op: "is", value: true }])).toBe(true);
    expect(matches(SLOW, [{ field: "tt_pass", op: "is", value: true }])).toBe(false);
    expect(matches(SLOW, [{ field: "tt_pass", op: "is", value: false }])).toBe(true);
    const grades: ScreenFilter = { field: "fund_grade", op: "in", values: ["A", "B"] };
    expect(applyFilters([SPOT, SLOW], [grades]).map((r) => r.symbol)).toEqual(["SPOT"]);
  });

  it("describes filters in words", () => {
    expect(describeFilter({ field: "rs_rating", op: "between", min: 85, max: null })).toBe(
      "RS Rating ≥ 85",
    );
    expect(describeFilter({ field: "close", op: "between", min: 10, max: 50.5 })).toBe(
      "Price 10–50.5",
    );
    expect(describeFilter({ field: "off_high_pct", op: "between", min: -25, max: null })).toBe(
      "From 52-week high ≥ -25%",
    );
    expect(describeFilter({ field: "tt_pass", op: "is", value: true })).toBe("Trend Template: yes");
    expect(describeFilter({ field: "pattern", op: "in", values: ["vcp", "flat_base"] })).toBe(
      "Pattern: VCP, Flat base",
    );
    expect(describeFilter(newFilter("group_rank"))).toBe("Group rank: any");
  });
});

describe("presets", () => {
  it("are the nine from the spec and only use known fields", () => {
    expect(PRESETS.map((p) => p.name)).toEqual([
      "Trend Template leaders",
      "VCPs near pivot",
      "Breakouts today",
      "Pocket pivots today",
      "Earnings gap-ups",
      "RS leads price",
      "High tight flags",
      "Top group leaders",
      "Fundamentals A + RS ≥ 90",
    ]);
    for (const p of PRESETS) {
      for (const f of p.filters) expect(FIELD_NAMES).toContain(f.field);
      if (p.sort) expect(FIELD_NAMES).toContain(p.sort.field);
    }
  });

  it("screen the universe only", () => {
    const leaders = PRESETS[0];
    expect(applyFilters([SPOT, SLOW, THIN], leaders.filters).map((r) => r.symbol)).toEqual([
      "SPOT",
    ]);
    const fundRs = PRESETS[8];
    expect(applyFilters([SPOT, SLOW, THIN], fundRs.filters).map((r) => r.symbol)).toEqual(["SPOT"]);
  });

  it("notice unsaved changes", () => {
    const p = PRESETS[0];
    expect(sameScreen(p, { ...p, name: "Renamed" })).toBe(true);
    expect(sameScreen(p, { ...p, sort: { field: "score", desc: true } })).toBe(false);
  });
});

describe("cells and sorting", () => {
  it("formats values for the table", () => {
    expect(display("close", SPOT)).toBe("94.00");
    expect(display("tt_passed", SPOT)).toBe("8/8");
    expect(display("readiness_pct", SPOT)).toBe("1.0% below");
    expect(display("pattern", SPOT)).toBe("VCP");
    expect(display("setup_state", SPOT)).toBe("Near pivot");
    expect(display("market_cap", row({ market_cap: 12_300_000_000 }))).toBe("$12.3B");
    expect(display("change_pct", row({ change_pct: -0.4 }))).toBe("−0.4%");
    expect(display("rs_rating", row({}))).toBe("—");
  });

  it("sorts grades best first and setup stages in lifecycle order", () => {
    expect(sortValue("fund_grade", SPOT)).toBeGreaterThan(sortValue("fund_grade", SLOW) as number);
    expect(sortValue("grade", SPOT)).toBe(83.5);
    expect(sortValue("setup_state", row({ setup_state: "near_pivot" }))).toBe(0);
    expect(sortValue("tt_pass", SPOT)).toBe(1);
    expect(sortValue("rs_rating", row({}))).toBeNull();
  });
});

describe("sparkPoints", () => {
  it("maps 0-255 bytes onto the box, low values at the bottom", () => {
    const encoded = btoa(String.fromCharCode(0, 255, 0));
    expect(sparkPoints(encoded, 10, 20)).toBe("0.0,19.0 5.0,1.0 10.0,19.0");
    expect(sparkPoints("", 10, 20)).toBe("");
  });
});

describe("toCsv", () => {
  it("writes raw values with the name beside the symbol and quotes when needed", () => {
    const csv = toCsv(
      [SPOT, row({ symbol: "BRK.B", name: 'Berkshire "B", Inc', close: 410.2 })],
      ["symbol", "spark", "close", "tt_pass"],
    );
    expect(csv).toBe(
      "symbol,name,close,tt_pass\r\n" +
        "SPOT,Spotify Technology S.A.,94,yes\r\n" +
        'BRK.B,"Berkshire ""B"", Inc",410.2,\r\n',
    );
  });
});
