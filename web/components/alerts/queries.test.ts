import { describe, expect, it } from "vitest";
import { alertHref } from "./queries";

describe("alertHref", () => {
  it("leads to the stock, the page the alert names, or the alerts centre", () => {
    expect(alertHref({ symbol: "SPOT", payload: { href: "/buy-list" } })).toBe("/stocks/SPOT");
    expect(alertHref({ symbol: null, payload: { href: "/buy-list" } })).toBe("/buy-list");
    expect(alertHref({ symbol: null, payload: {} })).toBe("/alerts");
    // Only paths inside the app.
    expect(alertHref({ symbol: null, payload: { href: "https://example.com" } })).toBe("/alerts");
    expect(alertHref({ symbol: null, payload: { href: "//example.com" } })).toBe("/alerts");
  });
});
