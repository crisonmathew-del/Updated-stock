"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useState } from "react";
import { AlertBell } from "@/components/alerts/alert-bell";
import { CommandPalette } from "@/components/shell/command-palette";
import { LiveProvider } from "@/components/shell/live-provider";
import { Shortcuts } from "@/components/shell/shortcuts";
import { Toaster } from "@/components/shell/toaster";
import { Change } from "@/components/ui/badges";
import { Kbd } from "@/components/ui/kbd";
import { api, type Quote, type Regime } from "@/lib/api";
import { formatPrice } from "@/lib/format";
import { applyTheme } from "@/lib/theme";
import { useTheme } from "@/lib/use-theme";
import { cn } from "@/lib/utils";
import { quoteLabel, useNewerQuote } from "@/stores/live";

// What fits in the bar (its content is capped at 1600 px): the main links from 768 px (phones and
// small tablets get the bottom tab bar), the review pages from 1280 px (in the menu below that),
// the regime from 1024 px and the index quotes, compact, from 1536 px.
const LINKS = [
  { href: "/", label: "Dashboard" },
  { href: "/screener", label: "Screener" },
  { href: "/watchlists", label: "Watchlists" },
  { href: "/live", label: "Live" },
  { href: "/holdings", label: "Holdings" },
];
const REVIEW = [
  { href: "/performance", label: "Performance" },
  { href: "/backtests", label: "Backtests" },
];
const ADMIN = [
  { href: "/settings", label: "Settings" },
  { href: "/admin/status", label: "Status" },
  { href: "/admin/data", label: "Data" },
  { href: "/admin/inspect", label: "Inspect" },
  { href: "/admin/patterns", label: "Patterns" },
  { href: "/admin/setups", label: "Setups" },
  { href: "/admin/signals", label: "Signals" },
];

const REGIME_TONE: Record<string, { tone: string; icon: string }> = {
  confirmed_uptrend: { tone: "border-rise text-rise", icon: "▲" },
  uptrend_under_pressure: { tone: "border-warn text-warn", icon: "◆" },
  correction: { tone: "border-fall text-fall", icon: "▼" },
};

function RegimePill() {
  const regime = useQuery({
    queryKey: ["market", "regime", 1],
    queryFn: () => api.get<Regime>("/api/market/regime?days=1"),
    staleTime: 5 * 60_000,
  });
  const r = regime.data;
  if (!r?.state) return null;
  const look = REGIME_TONE[r.state] ?? { tone: "border-border text-muted", icon: "•" };
  const dd = Math.max(0, ...r.indexes.map((i) => i.distribution_days));
  const detail = r.indexes.map((i) => `${i.symbol} ${i.distribution_days} DD`).join(" · ");
  return (
    <Link
      href="/#market"
      title={`Market regime: ${r.label}. Distribution days: ${detail}.`}
      className={cn(
        "hidden items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-xs whitespace-nowrap lg:inline-flex",
        look.tone,
      )}
    >
      <span aria-hidden>{look.icon}</span>
      {r.label}
      <span className="text-muted">· {dd} DD</span>
    </Link>
  );
}

function Quotes() {
  const quotes = useQuery({
    queryKey: ["market", "quotes"],
    queryFn: () => api.get<Quote[]>("/api/market/quotes"),
    staleTime: 5 * 60_000,
  });
  return (
    <ul className="hidden items-center gap-3 text-xs 2xl:flex" aria-label="Index quotes">
      {quotes.data?.map((q) => (
        <QuoteItem key={q.symbol} quote={q} />
      ))}
    </ul>
  );
}

/** An index's change today: live while the market is open, else at the close. */
function QuoteItem({ quote: q }: { quote: Quote }) {
  const live = useNewerQuote(q.symbol, q.date);
  return (
    <li
      className="flex gap-1.5 whitespace-nowrap"
      title={
        live
          ? `${q.symbol} ${formatPrice(live.last)} (${quoteLabel(live)})`
          : `${q.symbol} closed at ${formatPrice(q.close)}`
      }
    >
      <span className="text-muted">{q.symbol}</span>
      <Change value={live ? live.change_pct : q.change_pct} />
    </li>
  );
}

function AccountMenu() {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const me = useQuery({
    queryKey: ["auth", "me"],
    queryFn: () => api.get<{ email: string }>("/api/auth/me"),
    staleTime: 5 * 60_000,
  });
  async function signOut() {
    await api.post("/api/auth/logout").catch(() => undefined);
    router.replace("/login");
  }
  return (
    <div className="relative">
      <button
        type="button"
        aria-expanded={open}
        aria-haspopup="menu"
        onClick={() => setOpen(!open)}
        className="rounded-md px-2 py-1 text-sm text-muted hover:text-foreground"
      >
        Admin ▾
      </button>
      {open && (
        <div
          role="menu"
          className="absolute right-0 z-30 mt-1 w-56 rounded-md border border-border bg-surface p-1 text-sm shadow-lg"
          onMouseLeave={() => setOpen(false)}
        >
          {REVIEW.map(({ href, label }) => (
            <Link
              key={href}
              role="menuitem"
              href={href}
              onClick={() => setOpen(false)}
              className="block rounded px-2 py-1.5 hover:bg-surface-2 xl:hidden"
            >
              {label}
            </Link>
          ))}
          <div className="my-1 border-t border-border xl:hidden" />
          {ADMIN.map(({ href, label }) => (
            <Link
              key={href}
              role="menuitem"
              href={href}
              onClick={() => setOpen(false)}
              className="block rounded px-2 py-1.5 hover:bg-surface-2"
            >
              {label}
            </Link>
          ))}
          <div className="my-1 border-t border-border" />
          {me.data && <p className="truncate px-2 py-1 text-xs text-muted">{me.data.email}</p>}
          <button
            type="button"
            role="menuitem"
            onClick={signOut}
            className="block w-full rounded px-2 py-1.5 text-left hover:bg-surface-2"
          >
            Sign out
          </button>
        </div>
      )}
    </div>
  );
}

const TABS = [
  { href: "/", label: "Dashboard", icon: "⌂" },
  { href: "/screener", label: "Screener", icon: "▤" },
  { href: "/watchlists", label: "Watchlists", icon: "★" },
  { href: "/live", label: "Live", icon: "◉" },
  { href: "/holdings", label: "Holdings", icon: "◧" },
];

/** Phones: the main destinations and search as a tab bar along the bottom (spec §9). */
function TabBar({ pathname, onSearch }: { pathname: string | null; onSearch: () => void }) {
  const item = "flex min-w-0 flex-1 flex-col items-center gap-0.5 py-2 text-[11px]";
  return (
    <nav
      aria-label="Main"
      className="fixed inset-x-0 bottom-0 z-20 flex border-t border-border bg-background/95 pb-[env(safe-area-inset-bottom)] backdrop-blur md:hidden"
    >
      {TABS.map(({ href, label, icon }) => {
        const active = href === "/" ? pathname === "/" : pathname?.startsWith(href);
        return (
          <Link
            key={href}
            href={href}
            aria-current={active ? "page" : undefined}
            className={cn(item, active ? "text-foreground" : "text-muted")}
          >
            <span aria-hidden className="text-base leading-none">
              {icon}
            </span>
            {label}
          </Link>
        );
      })}
      <button type="button" onClick={onSearch} className={cn(item, "text-muted")}>
        <span aria-hidden className="text-base leading-none">
          ⌕
        </span>
        Search
      </button>
    </nav>
  );
}

export function TopBar() {
  const pathname = usePathname();
  const theme = useTheme();
  const [paletteOpen, setPaletteOpen] = useState(false);
  // The tab bar, toasts and palette sit outside <header>: its backdrop-filter would make it the
  // containing block for their `position: fixed`.
  return (
    <>
      <header className="sticky top-0 z-20 border-b border-border bg-background/95 backdrop-blur">
        <div className="mx-auto flex h-12 max-w-[1600px] items-center gap-4 px-4">
          <Link href="/" className="font-semibold tracking-tight">
            Breakout
          </Link>
          <nav className="hidden shrink-0 gap-1 text-sm md:flex" aria-label="Main">
            {[...LINKS, ...REVIEW].map(({ href, label }) => {
              const active = href === "/" ? pathname === "/" : pathname?.startsWith(href);
              const review = REVIEW.some((r) => r.href === href);
              return (
                <Link
                  key={href}
                  href={href}
                  aria-current={active ? "page" : undefined}
                  className={cn(
                    "rounded-md px-2 py-1 whitespace-nowrap",
                    review && "hidden xl:block",
                    active ? "text-foreground" : "text-muted hover:text-foreground",
                  )}
                >
                  {label}
                </Link>
              );
            })}
          </nav>
          <button
            type="button"
            onClick={() => setPaletteOpen(true)}
            className="flex h-8 min-w-0 flex-1 items-center gap-2 overflow-hidden rounded-md border border-border bg-surface px-2.5 text-sm text-muted hover:border-muted sm:max-w-xs"
          >
            <span aria-hidden>⌕</span>
            <span className="truncate">Search ticker or company</span>
            <span className="ml-auto hidden lg:inline">
              <Kbd>⌘K</Kbd>
            </span>
          </button>
          <div className="ml-auto flex shrink-0 items-center gap-3">
            <RegimePill />
            <Quotes />
            <AlertBell />
            <button
              type="button"
              onClick={() => applyTheme(theme === "dark" ? "light" : "dark")}
              aria-label={`Switch to ${theme === "dark" ? "light" : "dark"} theme`}
              title={`Switch to ${theme === "dark" ? "light" : "dark"} theme`}
              className="rounded-md px-1.5 py-1 text-muted hover:text-foreground"
            >
              <span aria-hidden>◐</span>
            </button>
            <AccountMenu />
          </div>
        </div>
      </header>
      <TabBar pathname={pathname} onSearch={() => setPaletteOpen(true)} />
      <CommandPalette open={paletteOpen} onOpenChange={setPaletteOpen} />
      <Shortcuts />
      <Toaster />
      <LiveProvider />
    </>
  );
}
