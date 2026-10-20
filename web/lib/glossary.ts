/**
 * Every term the app uses, in plain English (Phase 8). Shown by the "?" buttons next to
 * headings and column names (`InfoTip`) and on the Start here page.
 */
export type Term = { term: string; plain: string };

export const GLOSSARY = {
  pivot: {
    term: "Pivot (buy point)",
    plain:
      "The price at the top of a resting pattern (a base). When a stock closes above it on heavy buying, a new move up often starts. The app buys just above it.",
  },
  buy_zone: {
    term: "Buy zone",
    plain:
      "From the pivot to 5% above it. Inside it, the risk to the stop is still small; above it the stock is 'extended' and too late to buy.",
  },
  breakout: {
    term: "Breakout",
    plain:
      "A close above the pivot on at least 1.4× the usual volume, near the day's high. That shows big buyers stepping in. During the day it's 'provisional' until the close confirms it.",
  },
  stop: {
    term: "Stop (stop-loss)",
    plain:
      "The price where you sell if you're wrong, at most 8% below what you paid. Selling there keeps each loss small; never move it lower.",
  },
  buy_stop: {
    term: "Buy-stop order",
    plain:
      "An order you give your broker in advance: buy only if the price rises to a level. Placed at the entry just above the pivot, it buys the breakout as it happens, and does nothing if the stock never gets there.",
  },
  rs_rating: {
    term: "RS Rating",
    plain:
      "Relative strength, 1–99: how the stock did over the past year compared with every other stock. 90 means it beat 90% of them. Winners usually have 80 or more.",
  },
  rs_line: {
    term: "RS line",
    plain:
      "The stock's price divided by the market's (SPY). When it rises, the stock is beating the market. A new high in the RS line before the price is an early sign of strength.",
  },
  trend_template: {
    term: "Trend Template",
    plain:
      "8 checks that a stock is in a proper uptrend: price above its rising 50, 150 and 200-day averages, near its 1-year high, well above its 1-year low, and strong relative strength. 8/8 passes.",
  },
  stage: {
    term: "Stage (1–4)",
    plain:
      "Where the stock is in its long cycle: 1 resting after a fall, 2 advancing (the one to buy), 3 topping, 4 falling.",
  },
  setup_stage: {
    term: "Setup stage",
    plain:
      "Where the trade idea is: Watch (strong, no pattern yet) → Basing (pattern forming) → Near pivot (within 3% of the buy point) → Breakout → Extended (too late), or Failed / Invalidated.",
  },
  setup_score: {
    term: "Setup Score and grade",
    plain:
      "0–100 from six parts: trend, relative strength, fundamentals, pattern quality, industry group and buying volume. A+ is 90+, A 80+, B 70+, C 60+. In a falling market the score is halved, so nothing gets a grade.",
  },
  fundamentals: {
    term: "Fundamentals Grade",
    plain:
      "A to E from the company's SEC filings: growth in profits (earnings per share) and sales, profit margins and return on equity. A and B are strong; D and E weak.",
  },
  group_rank: {
    term: "Industry group rank",
    plain:
      "How strong the stock's industry is, ranked 1 (strongest) to about 250. Most big winners come from the top 40 groups.",
  },
  volume_ratio: {
    term: "Volume × average",
    plain:
      "The day's trading volume compared with the last 50 days' average. 2.0× is double the usual: heavy buying or selling.",
  },
  market_regime: {
    term: "Market regime",
    plain:
      "Whether the overall market is rising (confirmed uptrend), wobbling (under pressure) or falling (correction). Most breakouts fail in a correction, so the app holds back new buys then.",
  },
  distribution_day: {
    term: "Distribution day",
    plain:
      "A day the index falls 0.2% or more on higher volume than the day before: big investors selling. Many of them in a few weeks warn that the market is weakening.",
  },
  base: {
    term: "Base",
    plain:
      "A resting pattern after a rise, where the stock moves sideways for weeks while sellers run out. Bases: VCP, flat base, cup with handle, ascending base, high tight flag, three weeks tight.",
  },
  vcp: {
    term: "VCP (volatility contraction)",
    plain:
      "A base where each pullback is smaller than the last (say 20%, then 10%, then 5%) on drying-up volume: the stock gets quieter and tighter before it breaks out.",
  },
  pocket_pivot: {
    term: "Pocket pivot",
    plain:
      "An up day whose volume beats every down day of the last 10 sessions, often inside a base: a hint that big buyers are moving in. Not a buy point by itself.",
  },
  extended: {
    term: "Extended",
    plain:
      "More than 5% above the pivot: past the buy zone. Buying now means a wide stop. Don't chase.",
  },
  r_multiple: {
    term: "R (risk multiple)",
    plain:
      "Profit measured in units of what you risked. Risk $1 a share (entry to stop) and make $2: that's +2R. 2R and 3R are the first profit levels to watch.",
  },
  verdict: {
    term: "What to do",
    plain:
      "The app's rules applied to this stock today: Buy signal (it broke out and passes every rule), Get ready (place the order for tomorrow), Not yet (good, but no buy point yet) or Don't buy (with the reasons).",
  },
  breakout_watch: {
    term: "Breakout watch",
    plain:
      "Bases that look most likely to break out soon, scored 0–100 from the signs traders look for: close to the pivot, swings shrinking, volume drying up, the RS line rising, more buying than selling, closes near the day's highs. It ranks, it can't know: the app counts how often its picks really broke out.",
  },
  early_entry: {
    term: "Early entry",
    plain:
      "A buy point below the pivot, at the top of the last few days' tight range, with a stop under that range. You get in cheaper with less risk, but the stock may never break out. Riskier than buying the breakout.",
  },
  paper_trading: {
    term: "Paper trading",
    plain:
      "Practising with pretend money (for example in your Alpaca Paper account) so you can learn the method without risking anything.",
  },
} satisfies Record<string, Term>;

export type GlossaryKey = keyof typeof GLOSSARY;
