import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { alert } from "@/components/alerts/fixtures";
import { UNREAD } from "@/components/alerts/queries";
import { mockApi } from "@/test-utils";
import { useLive } from "@/stores/live";
import { useToasts } from "@/stores/toast";
import { LiveProvider } from "./live-provider";

class FakeSocket {
  static last: FakeSocket | null = null;
  onopen: (() => void) | null = null;
  onmessage: ((m: { data: string }) => void) | null = null;
  onclose: (() => void) | null = null;
  constructor() {
    FakeSocket.last = this;
  }
  send() {}
  close() {}
}

beforeEach(() => {
  vi.stubGlobal("WebSocket", FakeSocket);
  useLive.setState({
    connected: false,
    quotes: {},
    events: [],
    scans: { premarket: null, sweep: null },
  });
  useToasts.setState({ toasts: [] });
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("LiveProvider", () => {
  it("feeds quotes, setup events, scans, the bell and toasts from the socket", async () => {
    mockApi({
      "GET /api/live": {
        body: {
          session: "2026-10-02",
          through: "2026-10-01",
          quotes: { AAPL: { symbol: "AAPL", last: 250 } },
          events: [{ kind: "setup_stop", symbol: "TSM", at: "2026-10-02T14:00:00Z" }],
          premarket: { at: "x", items: [] },
          sweep: null,
        },
      },
    });
    const client = new QueryClient();
    render(
      <QueryClientProvider client={client}>
        <LiveProvider />
      </QueryClientProvider>,
    );
    const socket = FakeSocket.last!;
    await act(async () => {
      socket.onopen?.();
      await Promise.resolve();
    });
    expect(useLive.getState().connected).toBe(true);
    await vi.waitFor(() => expect(useLive.getState().quotes.AAPL?.last).toBe(250));

    act(() => {
      socket.onmessage?.({ data: JSON.stringify({ type: "hello", unread: 2 }) });
      socket.onmessage?.({
        data: JSON.stringify({
          type: "batch",
          events: [
            { type: "alert", data: alert(5) },
            { type: "quotes", data: [{ symbol: "SPOT", last: 92.65 }] },
            { type: "setup_event", data: { kind: "breakout_provisional", symbol: "SPOT" } },
            { type: "scan", scan: "sweep", data: { at: "y", items: [] } },
          ],
        }),
      });
    });
    expect(client.getQueryData(UNREAD.key)).toEqual({ count: 3 });
    expect(useLive.getState().quotes.SPOT?.last).toBe(92.65);
    expect(useLive.getState().events.map((e) => e.symbol)).toEqual(["SPOT", "TSM"]);
    expect(useLive.getState().scans.sweep).toEqual({ at: "y", items: [] });
    expect(useToasts.getState().toasts).toEqual([
      expect.objectContaining({
        text: "▲ SPOT breaking out strongly (provisional)",
        tone: "alert",
        href: "/stocks/SPOT",
      }),
    ]);
    act(() => socket.onclose?.());
    expect(useLive.getState().connected).toBe(false);
  });

  it("refetches what the page shows when new analytics land, or on a reconnect after they did", async () => {
    const live = (through: string, quotes: Record<string, unknown> = {}) => ({
      body: { session: "2026-10-08", through, quotes, events: [], premarket: null, sweep: null },
    });
    const routes = { "GET /api/live": live("2026-10-07", { AAPL: { symbol: "AAPL", last: 250 } }) };
    const fetchMock = mockApi(routes);
    const client = new QueryClient();
    const refetch = vi.spyOn(client, "invalidateQueries");
    render(
      <QueryClientProvider client={client}>
        <LiveProvider />
      </QueryClientProvider>,
    );
    const socket = FakeSocket.last!;
    const send = (events: unknown[]) =>
      act(() => socket.onmessage?.({ data: JSON.stringify({ type: "batch", events }) }));
    await act(async () => {
      socket.onopen?.();
      await Promise.resolve();
    });
    await vi.waitFor(() => expect(useLive.getState().quotes.AAPL?.last).toBe(250));
    expect(refetch).not.toHaveBeenCalled();

    // The evening update stored Oct 8: every query refetches and the day's live quotes go.
    send([{ type: "data", through: "2026-10-08" }]);
    expect(refetch).toHaveBeenCalledTimes(1);
    expect(useLive.getState().quotes).toEqual({});

    // A re-score of the same close: the page refetches, today's live data stays.
    send([{ type: "quotes", data: [{ symbol: "SPOT", last: 92.65 }] }]);
    send([{ type: "data", through: "2026-10-08" }]);
    expect(refetch).toHaveBeenCalledTimes(2);
    expect(useLive.getState().quotes.SPOT?.last).toBe(92.65);

    // Back after a sleep: the socket reconnects and Oct 9 has been processed meanwhile.
    routes["GET /api/live"] = live("2026-10-09");
    await act(async () => {
      socket.onopen?.();
      await Promise.resolve();
    });
    await vi.waitFor(() => expect(refetch).toHaveBeenCalledTimes(3));
    expect(useLive.getState().quotes).toEqual({});

    // Reconnecting with nothing new refetches nothing.
    const primes = fetchMock.mock.calls.length;
    await act(async () => {
      socket.onopen?.();
      await Promise.resolve();
    });
    await vi.waitFor(() => expect(fetchMock.mock.calls.length).toBe(primes + 1));
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    expect(refetch).toHaveBeenCalledTimes(3);
  });
});
