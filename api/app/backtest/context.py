"""Point-in-time inputs for the backtest's stage 1.

`MarketContext` holds what every stock shares (sessions, the market regime, industry group
ranks, ticker attributes); it is loaded once and sent to each worker process. `StockHistory`
holds one stock's whole history (bars and indicators, liquidity, Trend Template, earnings
releases, statements, insider trades and splits); workers load them in batches with
`load_histories`. Nothing here is filtered to "known by then": the walk (app.backtest.tape)
cuts every input at the session it evaluates, the same way the EOD pipeline's queries do.
"""

import bisect
import math
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import date, timedelta

import numpy as np
import numpy.typing as npt
import polars as pl

from app.core.calendar import sessions_back, sessions_between
from app.data.loaders import _read, read_frame
from app.fundamentals.grade import GradeResult, InsiderTrade, Split, StatementRow
from app.fundamentals.scan import GradeInputs
from app.market.regime import LABELS as REGIME_LABELS
from app.market.regime import RegimeState
from app.patterns.bars import INDICATORS, Bars, rolling_mean
from app.patterns.scan import PATTERN_SESSIONS
from app.scanner.evaluate import MarketDay, Technicals
from app.scanner.snapshot import SNAPSHOT_COLUMNS
from app.scanner.universe_filter import Liquidity, passes
from app.scoring.trend_template import evaluate_trend_template
from app.settings.schema import AppSettings

# Calendar days of sessions kept after the last walked session, for next-earnings distances.
ESTIMATE_MARGIN_DAYS = 200
COLUMNS = tuple(dict.fromkeys((*INDICATORS, *SNAPSHOT_COLUMNS)))


def _ids(values: Sequence[int]) -> str:
    return ",".join(str(int(v)) for v in values)


@dataclass(frozen=True)
class TickerInfo:
    symbol: str
    type: str
    sector: str | None
    group_id: int | None
    group_name: str | None


@dataclass
class MarketContext:
    sessions: list[date]  # from the first history session to past the last walked one
    regime: dict[date, MarketDay]
    corrections: set[date]
    group_ranks: dict[tuple[int, date], int]
    groups_ranked: dict[date, int]
    tickers: dict[int, TickerInfo]
    index: dict[date, int] = field(init=False)
    week_ends: set[date] = field(init=False)

    def __post_init__(self) -> None:
        self.index = {d: i for i, d in enumerate(self.sessions)}
        self.week_ends = {
            d
            for d, after in zip(self.sessions, self.sessions[1:], strict=False)
            if d.isocalendar()[:2] != after.isocalendar()[:2]
        }

    def back(self, day: date, count: int) -> date:
        """app.core.calendar.sessions_back for a session inside the context."""
        return self.sessions[self.index[day] - count]

    def sessions_to(self, day: date, later: date) -> int:
        """len(sessions_between(day, later)) - 1: sessions from `day` until `later`."""
        lo = bisect.bisect_left(self.sessions, day)
        hi = bisect.bisect_right(self.sessions, later)
        return max(0, hi - lo) - 1

    def walk(self, start: date, end: date) -> list[date]:
        return self.sessions[
            bisect.bisect_left(self.sessions, start) : bisect.bisect_right(self.sessions, end)
        ]


def history_start(start: date) -> date:
    """The first session a walk from `start` reads (the detection window, plus room)."""
    return sessions_back(start, PATTERN_SESSIONS + 5)


async def load_market(start: date, end: date) -> MarketContext:
    first = history_start(start)
    sessions = sessions_between(first, end + timedelta(days=ESTIMATE_MARGIN_DAYS))
    regime_rows = await read_frame(
        "SELECT date, state FROM market_regime_daily WHERE index_symbol = 'MARKET' "
        f"AND date <= '{end.isoformat()}'"
    )
    regime: dict[date, MarketDay] = {}
    for day, state in regime_rows.iter_rows():
        regime[day] = MarketDay(str(state), REGIME_LABELS[RegimeState(state)])
    corrections = {d for d, m in regime.items() if m.state == RegimeState.CORRECTION}
    ranks = await read_frame(
        "SELECT group_id, date, rank FROM group_rank_history "
        f"WHERE date BETWEEN '{start.isoformat()}' AND '{end.isoformat()}'"
    )
    group_ranks: dict[tuple[int, date], int] = {}
    counts: dict[date, int] = defaultdict(int)
    for gid, day, rank in ranks.iter_rows():
        group_ranks[(int(gid), day)] = int(rank)
        counts[day] += 1
    tickers = await read_frame(
        "SELECT t.id, t.symbol, t.type, t.sector, t.industry_group_id, g.name FROM tickers t "
        "LEFT JOIN industry_groups g ON g.id = t.industry_group_id "
        "WHERE t.type IN ('common', 'adr') AND NOT t.is_benchmark"
    )
    info = {
        int(tid): TickerInfo(
            str(symbol),
            str(kind),
            sector,
            None if gid is None else int(gid),
            None if name is None else str(name),
        )
        for tid, symbol, kind, sector, gid, name in tickers.iter_rows()
    }
    return MarketContext(sessions, regime, corrections, group_ranks, dict(counts), info)


@dataclass
class StockHistory:
    """One stock's history. `bars` holds the sessions with indicators (what the pipeline's
    joins return); every per-session array below is aligned with it."""

    ticker_id: int
    bars: Bars
    bar_days: set[date]  # sessions with any daily bar (with or without indicators)
    liquid: npt.NDArray[np.bool_]
    price: npt.NDArray[np.float64]  # as-traded close (for the liquidity filter)
    market_cap: npt.NDArray[np.float64]  # NaN without a share count
    columns: dict[str, list[object]]  # SNAPSHOT_COLUMNS + Trend Template, as Python values
    releases: list[tuple[date, str]]  # reported results releases, by date
    quarterly: list[StatementRow]  # sorted by reported date
    annual: list[StatementRow]
    insiders: list[InsiderTrade]  # purchases, sorted by filing date
    splits: list[Split]
    _tech: dict[int, Technicals] = field(default_factory=dict)
    _grade: dict[date, tuple[str | None, bool]] = field(default_factory=dict)

    def technicals(self, i: int) -> Technicals:
        """app.scanner.setups._technicals for session index `i`."""
        found = self._tech.get(i)
        if found is not None:
            return found
        c = self.columns

        def num(name: str) -> float | None:
            value = c[name][i]
            return None if value is None else float(value)  # type: ignore[arg-type]

        def whole(name: str) -> int | None:
            value = c[name][i]
            return None if value is None else int(value)  # type: ignore[call-overload]

        tech = Technicals(
            tt_passed=whole("tt_passed"),
            tt_pass=bool(c["tt_pass"][i]),
            stage=whole("stage"),
            rs_rating=whole("rs_rating"),
            rs_line_high_52w=bool(c["rs_line_high_52w"][i]),
            rs_new_high_ahead=bool(c["rs_new_high_ahead"][i]),
            rs_new_high_ahead_before=bool(c["ahead_before"][i]),
            rs_slope_63=num("rs_slope_63"),
            up_down_volume=num("up_down_volume_50"),
            volume_ratio=num("volume_ratio"),
            ema21=num("ema21"),
            sma50=num("sma50"),
        )
        self._tech[i] = tech
        return tech

    def grade(self, i: int, day: date, settings: AppSettings) -> tuple[str | None, bool]:
        """The Fundamentals Grade and insider cluster as app.fundamentals.scan would have
        stored them for `day` (statements by reported date, insider buys by filing date)."""
        found = self._grade.get(day)
        if found is not None:
            return found
        window = day - timedelta(days=settings.insider_cluster_window_days)
        up_down = self.columns["up_down_volume_50"][i]
        inputs = GradeInputs(
            quarterly=_known(self.quarterly, day),
            annual=_known(self.annual, day),
            up_down_volume=None if up_down is None else float(up_down),  # type: ignore[arg-type]
            insiders=[
                t for t in self.insiders if t.filed_date <= day and t.transaction_date > window
            ],
            splits=[s for s in self.splits if s.ex_date <= day],
        )
        result = inputs.grade(day, settings)
        out = (result.grade, _insider_cluster(result))
        self._grade[day] = out
        return out

    def window(self, start: date, end: date) -> Bars:
        """The sessions from `start` to `end` as the pipeline's Bars.from_frame builds them
        (its 10-day SMA starts over inside the window)."""
        cut = self.bars.window(start, end)
        out = replace(cut, sma10=rolling_mean(cut.close, 10))
        out.__dict__["days"] = cut.days
        return out


def _known(rows: list[StatementRow], day: date) -> list[StatementRow]:
    return rows[: bisect.bisect_right([r.reported_date for r in rows], day)]


def _insider_cluster(result: GradeResult) -> bool:
    return any(
        c.get("key") == "insider_bonus" and c.get("status") == "pass"
        for c in result.components_json()
    )


def _factor(ratios: list[float]) -> float | None:
    """exp(sum(ln(ratio))), as the liquidity query multiplies split ratios."""
    return math.exp(sum(math.log(r) for r in ratios)) if ratios else None


def _liquidity(
    ticker_type: str,
    days: list[date],
    close: npt.NDArray[np.float64],
    dollar_volume: list[object],
    splits: list[tuple[date, float]],
    shares: list[tuple[date, date, float]],
    settings: AppSettings,
) -> tuple[npt.NDArray[np.bool_], npt.NDArray[np.float64], npt.NDArray[np.float64]]:
    """app.scanner.universe_filter for every session: (passes, as-traded price, market cap)."""
    positive = [(d, r) for d, r in splits if r > 0]
    filings = sorted(shares)  # (filed, as_of, shares): the last filed by then wins
    filed = [f[0] for f in filings]
    out = np.zeros(len(days), dtype=np.bool_)
    price = np.empty(len(days), dtype=np.float64)
    caps = np.full(len(days), np.nan)
    for i, day in enumerate(days):
        factor = _factor([r for d, r in positive if d > day])
        price[i] = float(close[i]) * (factor if factor is not None else 1)
        k = bisect.bisect_right(filed, day) - 1
        cap = None
        if k >= 0:
            _, as_of, count = filings[k]
            share_factor = _factor([r for d, r in positive if d > as_of])
            cap = float(close[i]) * count * (share_factor if share_factor is not None else 1)
            caps[i] = cap
        volume = dollar_volume[i]
        out[i] = passes(
            Liquidity(
                0,
                ticker_type,
                float(price[i]),
                None if volume is None else float(volume),  # type: ignore[arg-type]
                cap,
            ),
            settings,
        )
    return out, price, caps


def _trend_template(
    frame: pl.DataFrame, every_close: pl.DataFrame, market: MarketContext, settings: AppSettings
) -> pl.DataFrame:
    """app.scanner.snapshot.indicators_on for every session of one stock: the 200-day SMA
    `ma200_uptrend_lookback_days` sessions earlier and the previous session's close and RS
    flag come from those calendar sessions (null when the stock had no row then)."""
    lookback = settings.ma200_uptrend_lookback_days
    days = frame["date"].to_list()
    ago = [market.back(d, lookback) if market.index.get(d, -1) >= lookback else None for d in days]
    before = [market.back(d, 1) if market.index.get(d, -1) >= 1 else None for d in days]
    frame = frame.with_columns(
        pl.Series("ago_date", ago, dtype=pl.Date), pl.Series("prev_date", before, dtype=pl.Date)
    )
    frame = frame.join(
        frame.select(pl.col("date").alias("ago_date"), pl.col("sma200").alias("sma200_ago")),
        on="ago_date",
        how="left",
    )
    frame = frame.join(
        frame.select(
            pl.col("date").alias("prev_date"), pl.col("rs_new_high_ahead").alias("ahead_before")
        ),
        on="prev_date",
        how="left",
    )
    frame = frame.join(
        every_close.select(pl.col("date").alias("prev_date"), pl.col("close").alias("prev_close")),
        on="prev_date",
        how="left",
    ).sort("date")
    has_history = frame["sma200"].is_not_null()
    evaluated = evaluate_trend_template(frame, settings)
    return evaluated.with_columns(
        pl.when(has_history).then(pl.col("tt_passed").cast(pl.Int16)).alias("tt_passed")
    )


def load_histories(
    ticker_ids: Sequence[int],
    market: MarketContext,
    settings: AppSettings,
    first: date,
    end: date,
) -> list[StockHistory]:
    """Every input for these stocks from `first` to `end` (synchronous: runs in workers)."""
    if not ticker_ids:
        return []
    ids = _ids(ticker_ids)
    columns = ", ".join(f"i.{c}" for c in COLUMNS)
    frame = _read(
        "SELECT b.ticker_id, b.date, b.open, b.high, b.low, b.close, b.volume, "
        f"(i.ticker_id IS NOT NULL) AS has_ind, i.avg_dollar_volume_50 AS dollar_volume, "
        f"{columns} FROM daily_bars b LEFT JOIN indicators_daily i "
        "ON i.ticker_id = b.ticker_id AND i.date = b.date "
        f"WHERE b.ticker_id IN ({ids}) AND b.date BETWEEN '{first.isoformat()}' "
        f"AND '{end.isoformat()}' ORDER BY b.ticker_id, b.date"
    )
    splits: dict[int, list[tuple[date, float]]] = defaultdict(list)
    for tid, day, ratio in _read(
        "SELECT ticker_id, ex_date, value FROM corporate_actions "
        f"WHERE kind = 'split' AND ticker_id IN ({ids})"
    ).iter_rows():
        splits[int(tid)].append((day, float(ratio)))
    shares: dict[int, list[tuple[date, date, float]]] = defaultdict(list)
    for tid, filed, as_of, count in _read(
        "SELECT ticker_id, filed_date, as_of_date, shares FROM shares_outstanding "
        f"WHERE ticker_id IN ({ids}) AND filed_date <= '{end.isoformat()}'"
    ).iter_rows():
        shares[int(tid)].append((filed, as_of, float(count)))
    releases: dict[int, list[tuple[date, str]]] = defaultdict(list)
    for tid, day, timing in _read(
        "SELECT ticker_id, report_date, timing FROM earnings_calendar "
        f"WHERE ticker_id IN ({ids}) AND status = 'reported' "
        f"AND report_date <= '{end.isoformat()}' ORDER BY report_date"
    ).iter_rows():
        releases[int(tid)].append((day, str(timing)))
    statements = {
        table: _statements(table, ids, end)
        for table in ("fundamentals_quarterly", "fundamentals_annual")
    }
    insiders: dict[int, list[InsiderTrade]] = defaultdict(list)
    for r in _read(
        "SELECT ticker_id, transaction_date, filed_date, insider_cik, insider_name, code, "
        "is_director, is_officer FROM insider_transactions "
        f"WHERE ticker_id IN ({ids}) AND code = 'P' AND filed_date <= '{end.isoformat()}' "
        "ORDER BY filed_date"
    ).iter_rows(named=True):
        insiders[int(r["ticker_id"])].append(
            InsiderTrade(
                r["transaction_date"],
                r["filed_date"],
                r["insider_cik"],
                r["insider_name"],
                r["code"],
                bool(r["is_director"]),
                bool(r["is_officer"]),
            )
        )

    out: list[StockHistory] = []
    if frame.is_empty():
        return out
    for (tid,), rows in frame.partition_by("ticker_id", as_dict=True).items():
        ticker = int(tid)
        info = market.tickers.get(ticker)
        if info is None:
            continue
        with_ind = rows.filter(pl.col("has_ind"))
        if with_ind.is_empty():
            continue
        evaluated = _trend_template(with_ind, rows.select("date", "close"), market, settings)
        bars = Bars.from_frame(evaluated, market.corrections)
        liquid, price, caps = _liquidity(
            info.type,
            bars.dates,
            bars.close,
            evaluated["dollar_volume"].to_list(),
            splits.get(ticker, []),
            shares.get(ticker, []),
            settings,
        )
        keep = (
            *SNAPSHOT_COLUMNS,
            "tt_passed",
            "tt_pass",
            "ahead_before",
            "prev_close",
            "close",
            "volume",
            "dollar_volume",
        )
        out.append(
            StockHistory(
                ticker_id=ticker,
                bars=bars,
                bar_days=set(rows["date"].to_list()),
                liquid=liquid,
                price=price,
                market_cap=caps,
                columns={name: evaluated[name].to_list() for name in dict.fromkeys(keep)},
                releases=releases.get(ticker, []),
                quarterly=statements["fundamentals_quarterly"].get(ticker, []),
                annual=statements["fundamentals_annual"].get(ticker, []),
                insiders=insiders.get(ticker, []),
                splits=[Split(d, r) for d, r in splits.get(ticker, [])],
            )
        )
    return out


def _statements(table: str, ids: str, end: date) -> dict[int, list[StatementRow]]:
    frame = _read(
        "SELECT ticker_id, period_end, reported_date, fiscal_year, fiscal_period, "
        "coalesce(eps_diluted, eps_basic) AS eps, revenue, net_income, operating_income, "
        f"equity, derived, currency FROM {table} "
        f"WHERE ticker_id IN ({ids}) AND reported_date <= '{end.isoformat()}' "
        "ORDER BY reported_date"
    )
    out: dict[int, list[StatementRow]] = defaultdict(list)
    for r in frame.iter_rows(named=True):
        out[int(r["ticker_id"])].append(
            StatementRow(
                period_end=r["period_end"],
                reported_date=r["reported_date"],
                fiscal_year=r["fiscal_year"],
                fiscal_period=r["fiscal_period"],
                eps=r["eps"],
                revenue=r["revenue"],
                net_income=r["net_income"],
                operating_income=r["operating_income"],
                equity=r["equity"],
                derived=bool(r["derived"]),
                currency=r["currency"],
            )
        )
    return out
