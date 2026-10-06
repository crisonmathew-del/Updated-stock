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
          quotes: { AAPL: { symbol: "AAPL", last: 250 } },
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
    expect(useLive.getState().events[0]?.symbol).toBe("SPOT");
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
});
