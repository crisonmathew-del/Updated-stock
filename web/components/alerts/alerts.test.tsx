import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AlertBell } from "@/components/alerts/alert-bell";
import { AlertChannels } from "@/components/alerts/channels";
import { AlertHistory, deliveryLabel } from "@/components/alerts/history";
import { AlertRules, ruleInput, suggestName, type Draft } from "@/components/alerts/rules";
import { mockApi, renderWithClient } from "@/test-utils";
import { alert, page, rule } from "./fixtures";

afterEach(() => {
  vi.unstubAllGlobals();
});

function bodyOf(fetchMock: ReturnType<typeof mockApi>, method: string, path: string): unknown {
  const call = fetchMock.mock.calls.find(
    ([url, init]) => url === path && (init?.method ?? "GET") === method,
  );
  return call ? JSON.parse(String(call[1]?.body)) : undefined;
}

describe("AlertBell", () => {
  it("shows the unread count, the latest alerts and marks them read", async () => {
    const fetchMock = mockApi({
      "GET /api/alerts/unread": { body: { count: 2 } },
      "GET /api/alerts?limit=8": {
        body: page([
          alert(2),
          alert(1, {
            read: true,
            priority: "normal",
            title: "AAPL is near its pivot",
            symbol: "AAPL",
          }),
        ]),
      },
      "POST /api/alerts/read": { body: { count: 0 } },
    });
    renderWithClient(<AlertBell />);
    const bell = await screen.findByRole("button", { name: "Alerts, 2 unread" });
    expect(bell).toHaveTextContent("2");
    fireEvent.click(bell);
    const latest = await screen.findByRole("link", { name: /SPOT breaking out strongly/ });
    expect(latest).toHaveAttribute("href", "/stocks/SPOT");
    expect(within(latest).getByRole("img", { name: "High priority" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /AAPL is near its pivot/ })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Mark all read" }));
    await screen.findByRole("button", { name: "Alerts" });
    expect(bodyOf(fetchMock, "POST", "/api/alerts/read")).toEqual({});
  });
});

describe("AlertHistory", () => {
  it("lists alerts with what each channel did and filters by priority", async () => {
    const fetchMock = mockApi({
      "GET /api/alerts?limit=50": {
        body: page([
          alert(3),
          alert(2, {
            priority: "normal",
            kind: "near_pivot",
            kind_label: "Near pivot",
            title: "AAPL is near its pivot",
            symbol: "AAPL",
            delivery: { in_app: "sent", email: "digest" },
          }),
          alert(1, { delivery: { in_app: "sent", email: "failed: SMTP refused" }, read: true }),
        ]),
      },
      "GET /api/alerts?priority=high&limit=50": { body: page([alert(3)]) },
      "POST /api/alerts/read": { body: { count: 1 } },
    });
    renderWithClient(<AlertHistory />);
    const list = await screen.findByRole("list", { name: "Alerts" });
    const rows = await within(list).findAllByRole("listitem");
    expect(rows).toHaveLength(3);
    expect(rows[0]).toHaveTextContent("Email sent");
    expect(rows[1]).toHaveTextContent("Email in the next digest");
    expect(rows[2]).toHaveTextContent("Email failed");
    expect(within(rows[2]).getByTitle("failed: SMTP refused")).toBeInTheDocument();
    fireEvent.click(within(rows[0]).getByRole("button", { name: "Mark read" }));
    await waitFor(() =>
      expect(bodyOf(fetchMock, "POST", "/api/alerts/read")).toEqual({ ids: [3] }),
    );

    fireEvent.change(screen.getByLabelText("Priority"), { target: { value: "high" } });
    await waitFor(() =>
      expect(
        within(screen.getByRole("list", { name: "Alerts" })).getAllByRole("listitem"),
      ).toHaveLength(1),
    );
  });

  it("says what each delivery outcome means", () => {
    expect(deliveryLabel("email", "daily digest: sent").text).toBe("Email in the daily digest");
    expect(deliveryLabel("email", "held: quiet hours").text).toBe("Email held (quiet hours)");
    expect(deliveryLabel("email", "not configured: set SMTP_HOST").text).toBe("Email not set up");
    expect(deliveryLabel("in_app", "off for this rule").text).toBe("In-app off");
  });
});

const DRAFT: Draft = {
  scope: "ticker",
  symbol: "spot",
  watchlistId: "",
  screenId: "",
  condition: "change_below",
  value: "4",
  ma: "sma50",
  inApp: true,
  email: false,
  priority: "normal",
  name: "",
};

describe("rule builder", () => {
  it("turns the form into a rule: down 4% is stored as −4", () => {
    expect(suggestName(DRAFT)).toBe("SPOT down 4%");
    expect(ruleInput(DRAFT)).toEqual({
      name: "SPOT down 4%",
      scope: "ticker",
      symbol: "SPOT",
      watchlist_id: null,
      screen_id: null,
      condition: "change_below",
      value: -4,
      ma: null,
      channels: ["in_app"],
      priority: "normal",
    });
    const lists = [{ id: 7, name: "Leaders", position: 0, items: [] }];
    const ma = {
      ...DRAFT,
      scope: "watchlist" as const,
      watchlistId: "7",
      condition: "ma_cross_below" as const,
      value: "",
    };
    expect(suggestName(ma, lists)).toBe("Leaders: below 50-day SMA");
    expect(ruleInput(ma, lists)).toMatchObject({
      watchlist_id: 7,
      symbol: null,
      value: null,
      ma: "sma50",
    });
  });

  it("creates a rule and lists yours with their descriptions", async () => {
    const fetchMock = mockApi({
      "GET /api/alert-rules": { body: [rule(1)] },
      "GET /api/watchlists": { body: [] },
      "GET /api/screens": { body: [] },
      "POST /api/alert-rules": { status: 201, body: rule(2, { name: "NVDA above 140" }) },
    });
    renderWithClient(<AlertRules initialSymbol="NVDA" />);
    expect(await screen.findByText(rule(1).description)).toBeInTheDocument();
    const form = screen.getByRole("form", { name: "New alert rule" });
    expect(within(form).getByLabelText("Stock")).toHaveValue("NVDA");
    fireEvent.change(within(form).getByLabelText("Price"), { target: { value: "140" } });
    fireEvent.click(within(form).getByRole("button", { name: "Create rule" }));
    await waitFor(() =>
      expect(bodyOf(fetchMock, "POST", "/api/alert-rules")).toMatchObject({
        name: "NVDA above 140",
        scope: "ticker",
        symbol: "NVDA",
        condition: "price_above",
        value: 140,
        channels: ["in_app", "email"],
        priority: "high",
      }),
    );
  });

  it("shows why the API refused a rule", async () => {
    mockApi({
      "GET /api/alert-rules": { body: [] },
      "GET /api/watchlists": { body: [] },
      "GET /api/screens": { body: [] },
      "POST /api/alert-rules": { status: 422, body: { detail: "No stock NOPE." } },
    });
    renderWithClient(<AlertRules initialSymbol="NOPE" />);
    const form = await screen.findByRole("form", { name: "New alert rule" });
    fireEvent.change(within(form).getByLabelText("Price"), { target: { value: "10" } });
    fireEvent.click(within(form).getByRole("button", { name: "Create rule" }));
    expect(await within(form).findByRole("alert")).toHaveTextContent("No stock NOPE.");
  });
});

describe("AlertChannels", () => {
  const SETTINGS = {
    items: [
      { key: "alerts_email_enabled", value: true },
      { key: "alert_email_immediate_priority", value: "high" },
      { key: "quiet_hours_start", value: "" },
      { key: "quiet_hours_end", value: "" },
      { key: "daily_digest_enabled", value: true },
      { key: "daily_digest_time", value: "17:30" },
      { key: "weekly_digest_enabled", value: true },
      { key: "alert_min_grade", value: "A" },
      { key: "alert_cooldown_minutes", value: 390 },
    ],
  };
  const STATUS = {
    email: {
      configured: false,
      provider: null,
      detail: "set RESEND_API_KEY and EMAIL_FROM, or SMTP_HOST, in .env",
    },
    streamer: {
      alive: true,
      state: "idle",
      provider: "none",
      detail: "STREAM_PROVIDER=none",
      since: null,
    },
    quiet_hours_now: false,
  };

  it("shows delivery status, saves settings and sends tests", async () => {
    const fetchMock = mockApi({
      "GET /api/settings": { body: SETTINGS },
      "GET /api/alerts/status": { body: STATUS },
      "PATCH /api/settings": { body: SETTINGS },
      "POST /api/alerts/test": {
        body: alert(9, {
          kind: "test",
          delivery: { in_app: "off for this rule", email: "not configured: set SMTP_HOST" },
        }),
      },
    });
    renderWithClient(<AlertChannels />);
    expect(await screen.findByText(/Not set up: set RESEND_API_KEY/)).toBeInTheDocument();
    expect(screen.getByText(/Idle \(no live feed configured\)/)).toBeInTheDocument();
    const form = screen.getByRole("form", { name: "Alert settings" });
    fireEvent.click(within(form).getByLabelText("Quiet hours"));
    fireEvent.change(within(form).getByLabelText("Quiet from"), { target: { value: "21:30" } });
    fireEvent.click(within(form).getByRole("button", { name: "Save settings" }));
    await waitFor(() =>
      expect(bodyOf(fetchMock, "PATCH", "/api/settings")).toMatchObject({
        changes: {
          quiet_hours_start: "21:30",
          quiet_hours_end: "07:00",
          daily_digest_time: "17:30",
        },
      }),
    );
    fireEvent.click(screen.getByRole("button", { name: "Send a test email" }));
    expect(await screen.findByRole("status")).toHaveTextContent(
      "– Email not set up (not configured: set SMTP_HOST).",
    );
  });
});
