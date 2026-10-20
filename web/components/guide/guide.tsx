"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import type { ReactNode } from "react";
import { ACTION_LOOK } from "@/components/stock/verdict-card";
import { Section } from "@/components/ui/section";
import { api, type Action, type SettingItem } from "@/lib/api";
import { easternToLocalHHMM, localZoneName } from "@/lib/format";
import { GLOSSARY, type GlossaryKey } from "@/lib/glossary";
import { useIsClient } from "@/lib/use-is-client";
import { cn } from "@/lib/utils";
import { SETTINGS_QUERY } from "./queries";

/** The settings the guide quotes, with their defaults for before they load. */
const DEFAULTS = {
  account_size: 100_000,
  risk_per_trade_pct: 1,
  max_stop_loss_pct: 8,
  buy_zone_max_pct_above_pivot: 5,
  decision_regimes: "confirmed_uptrend",
  decision_under_pressure_size_pct: 50,
  decision_min_setup_grade: "B",
  decision_min_fundamentals_grade: "C",
  decision_min_rs_rating: 80,
  decision_earnings_block_sessions: 10,
  decision_buy_window_sessions: 3,
  watch_min_score: 65,
  watch_hit_sessions: 10,
};
type Quoted = typeof DEFAULTS;

function useQuoted(): Quoted {
  const q = useQuery({
    queryKey: SETTINGS_QUERY.key,
    queryFn: () => api.get<{ items: SettingItem[] }>(SETTINGS_QUERY.path),
    staleTime: 5 * 60_000,
  });
  const values: Record<string, unknown> = { ...DEFAULTS };
  for (const item of q.data?.items ?? []) {
    if (item.key in DEFAULTS) values[item.key] = item.value;
  }
  return values as Quoted;
}

const VERDICTS: { action: Action; headline: string; text: string }[] = [
  {
    action: "buy",
    headline: "Buy signal",
    text: "It broke out (closed above its pivot on heavy buying) and passes every rule. You can still buy it the next day, as long as it trades inside the buy zone.",
  },
  {
    action: "get_ready",
    headline: "Get ready",
    text: "It's just below its pivot and passes every rule. Place the buy-stop order it shows the evening before, so it buys the breakout as it happens.",
  },
  {
    action: "wait",
    headline: "Not yet",
    text: "A good stock, but there's no buy point yet: it's still basing, or its breakout is already under way. Keep it on your watchlist.",
  },
  {
    action: "avoid",
    headline: "Don't buy",
    text: "It fails at least one rule (the card says which, with the numbers), or it has run too far above its pivot.",
  },
];

function Step({ n, title, children }: { n: number; title: string; children: ReactNode }) {
  return (
    <li className="flex gap-3">
      <span
        aria-hidden
        className="tabular flex size-6 shrink-0 items-center justify-center rounded-full border border-tide text-xs text-tide-ink"
      >
        {n}
      </span>
      <div className="flex flex-col gap-1">
        <p className="font-medium">{title}</p>
        <div className="text-sm text-muted">{children}</div>
      </div>
    </li>
  );
}

function Term({ term }: { term: GlossaryKey }) {
  return (
    <a href={`#term-${term}`} className="text-foreground underline underline-offset-2">
      {GLOSSARY[term].term.toLowerCase()}
    </a>
  );
}

/** The close, 16:00 New York time, in the viewer's clock (after hydration: the server can't
 * know their zone). */
function CloseTime() {
  const client = useIsClient();
  if (!client) return <>16:00 New York time</>;
  return (
    <>
      16:00 New York time ({easternToLocalHHMM("16:00")} {localZoneName()} for you)
    </>
  );
}

/** Start here (Phase 8): how to use the app day to day, in plain English, and every term. */
export function Guide() {
  const s = useQuoted();
  const risk = (s.account_size * s.risk_per_trade_pct) / 100;
  const regimes =
    s.decision_regimes === "confirmed_uptrend"
      ? "only while the market is in a confirmed uptrend"
      : `in a confirmed uptrend, or at ${s.decision_under_pressure_size_pct}% of the size when the uptrend is under pressure`;
  const rules: [string, string][] = [
    ["The market", `New buys ${regimes}. Most breakouts fail in a falling market.`],
    [
      "Quality",
      `A Setup grade of ${s.decision_min_setup_grade} or better (judged before the market adjustment).`,
    ],
    ["Trend", "All 8 Trend Template checks pass: a proper uptrend."],
    [
      "Strength",
      `An RS Rating of ${s.decision_min_rs_rating} or more: stronger than ${s.decision_min_rs_rating}% of stocks over the past year.`,
    ],
    [
      "The business",
      `A Fundamentals Grade of ${s.decision_min_fundamentals_grade} or better from its SEC filings (unknown counts as a fail).`,
    ],
    [
      "The stop",
      `A sensible stop at most ${s.max_stop_loss_pct}% below the entry, so a loss stays small.`,
    ],
    [
      "Earnings",
      s.decision_earnings_block_sessions > 0
        ? `No results due within ${s.decision_earnings_block_sessions} sessions: results can make a stock jump or fall 20% overnight.`
        : "Ignored (switched off in Settings).",
    ],
  ];
  return (
    <div className="flex max-w-3xl flex-col gap-4">
      <header className="flex flex-col gap-2">
        <h1 className="text-xl font-semibold">Start here</h1>
        <p className="text-sm">
          Breakout looks for strong growth stocks that are resting in a pattern (a{" "}
          <Term term="base" />) during an uptrend, and tells you when one is ready to break out of
          it. Its rules come from methods traders have used for decades (CAN SLIM, Minervini,
          Weinstein). It never places trades: you decide, and you place the orders at your broker.
        </p>
        <p className="text-sm text-muted">
          No one can know which stocks will go up. The rules aim for small losses and bigger wins,
          not for being right every time: expect many small losses. Learn with{" "}
          <Term term="paper_trading" /> first. This is a tool, not financial advice.
        </p>
      </header>

      <Section title="Every day, in 6 steps">
        <ol className="flex flex-col gap-4">
          <Step n={1} title="In the evening, open the Buy list">
            The US market closes at <CloseTime />. The app updates soon after and, when
            there&rsquo;s something to do, sends you a &ldquo;Buy list&rdquo; alert. Read the market
            line first: if it says no new buys, don&rsquo;t buy anything.
          </Step>
          <Step n={2} title="Place the orders it lists">
            Each &ldquo;Get ready&rdquo; stock shows a <Term term="buy_stop" /> to give your broker
            for the next session: how many shares, at what price, and the most to pay. If the price
            never gets there, nothing is bought. Once an order fills, place the <Term term="stop" />{" "}
            it shows straight away.
          </Step>
          <Step n={3} title="During the day, glance at Live">
            Breakouts show as they happen (provisional until the close confirms them on heavy
            volume). A &ldquo;Buy signal&rdquo; stays a buy for {s.decision_buy_window_sessions}{" "}
            sessions while it trades inside the <Term term="buy_zone" />; above that it&rsquo;s{" "}
            <Term term="extended" />: don&rsquo;t chase it.
          </Step>
          <Step n={4} title="Add what you buy to Holdings">
            The app then watches it for you and warns you when its rules say sell: the stop is hit,
            it closes below its 50-day average, it reaches the profit zone, or results are near.
          </Step>
          <Step n={5} title="Always sell at the stop">
            Every order is sized so hitting the stop costs about {s.risk_per_trade_pct}% of your
            account ({risk.toLocaleString("en-US", { maximumFractionDigits: 0 })} of{" "}
            {s.account_size.toLocaleString("en-US")}, set in{" "}
            <Link href="/settings" className="text-foreground underline underline-offset-2">
              Settings
            </Link>
            ). Never move the stop lower. Small losses are the price of the method.
          </Step>
          <Step n={6} title="Use the breakout watch to get ready">
            It ranks the bases most likely to break out soon (readiness {s.watch_min_score}/100 or
            more). It&rsquo;s a ranking, not a prediction: the Buy list shows how many of its picks
            really broke out within {s.watch_hit_sessions} sessions. Some show an{" "}
            <Term term="early_entry" />: cheaper, but the stock may never break out.
          </Step>
        </ol>
      </Section>

      <Section title="What the verdicts mean" help="verdict">
        <ul className="flex flex-col gap-3 text-sm">
          {VERDICTS.map((v) => (
            <li key={v.action} className="flex gap-3">
              <span className={cn("w-24 shrink-0 font-semibold", ACTION_LOOK[v.action].tone)}>
                <span aria-hidden>{ACTION_LOOK[v.action].icon}</span> {v.headline}
              </span>
              <span>{v.text}</span>
            </li>
          ))}
        </ul>
      </Section>

      <Section title="The 7 rules for a buy" note="Change them in Settings → Buy decisions">
        <ol className="flex list-decimal flex-col gap-2 pl-5 text-sm">
          {rules.map(([name, text]) => (
            <li key={name}>
              <span className="font-medium">{name}:</span> {text}
            </li>
          ))}
        </ol>
      </Section>

      <Section title="Every term, in plain English">
        <dl className="flex flex-col gap-3 text-sm">
          {(Object.keys(GLOSSARY) as GlossaryKey[]).map((key) => (
            <div key={key} id={`term-${key}`} className="scroll-mt-16">
              <dt className="font-medium">{GLOSSARY[key].term}</dt>
              <dd className="text-muted">{GLOSSARY[key].plain}</dd>
            </div>
          ))}
        </dl>
      </Section>
    </div>
  );
}
