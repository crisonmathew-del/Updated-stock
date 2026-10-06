"""One stock, one session: move its setup through the lifecycle, score it, plan it and decide
which signals the session produced. Pure: everything known at the session's close comes in,
the writes come out (app.scanner.setups loads and stores them).

Rules (spec §6.10, plus the choices approved for Phase 4):
- A stock has at most one active setup. Candidates are the bases detected today (forming, or
  broken out within the last few sessions) and earnings gaps still holding, best quality
  first; bases before gaps. A pattern whose setup already ended (failed, invalidated,
  superseded) is never tracked again.
- No setup yet: open one for the best candidate; otherwise a trend leader (all eight Trend
  Template checks, which include RS ≥ rs_rating_min) opens a WATCH setup.
- WATCH: a base (or gap) appearing converts the setup in place.
- Before a breakout: the setup keeps its pattern while the detector still reports it (the
  pivot and low follow the latest detection); if it stops being reported but another base is
  current, the setup switches to that base and says so. Otherwise the lifecycle decides.
- After a breakout: the lifecycle decides (stop, failed breakout, trailing exit, extended).
  A new base that started after the breakout supersedes the setup: it closes and a new one
  opens.
- A pattern first seen after it had already broken out (the first run, or a pattern the
  detector only recognises at the breakout) is replayed from its breakout session, so the
  breakout is judged on that day's volume and close, never today's.
- Trade plans are recomputed each session before the breakout and frozen at it.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import numpy as np

from app.patterns.bars import Bars
from app.patterns.types import EVENTS, LABELS, PatternMatch, PatternType, Status
from app.risk.trade_plan import build_trade_plan
from app.scoring.lifecycle import (
    POST_BREAKOUT,
    TERMINAL,
    Session,
    SetupFacts,
    State,
    Transition,
    next_state,
)
from app.scoring.red_flags import find_red_flags
from app.scoring.setup_score import ScoreInputs, SetupScore, readiness_pct, score_setup
from app.scoring.triggers import Trigger, pullback_to_average, undercut_and_rally
from app.settings.schema import AppSettings

SIGNAL_LABELS = {  # every signal type (spec §7.2), as the UI names it
    "new_top_setup": "New A/A+ setup",
    "near_pivot": "Near pivot",
    "breakout_provisional": "Breakout (provisional)",
    "breakout": "Breakout confirmed",
    "breakout_rejected": "Breakout rejected",
    "extended": "Extended",
    "failed": "Breakout failed",
    "invalidated": "Setup invalidated",
    "pocket_pivot": "Pocket pivot",
    "earnings_gap": "Earnings gap",
    "rs_new_high_ahead": "RS line new high ahead of price",
    "pullback": "Pullback buy point",
    "undercut_rally": "Undercut & rally",
    "regime_change": "Market regime change",
}
PRE_BREAKOUT = frozenset({State.BASING, State.NEAR_PIVOT})
CURRENT = frozenset({Status.FORMING, Status.BROKEN_OUT})
GRADE_ORDER = {"A+": 4, "A": 3, "B": 2, "C": 1}
TOP_GRADES = frozenset({"A+", "A"})
FAR_PAST = 10_000  # sessions since a breakout older than the loaded bars


@dataclass(frozen=True)
class Technicals:
    """The as-of session's indicator row (plus the Trend Template evaluated on it)."""

    tt_passed: int | None = None
    tt_pass: bool = False
    stage: int | None = None
    rs_rating: int | None = None
    rs_line_high_52w: bool = False
    rs_new_high_ahead: bool = False
    rs_new_high_ahead_before: bool = False  # on the previous session
    rs_slope_63: float | None = None
    up_down_volume: float | None = None
    volume_ratio: float | None = None
    ema21: float | None = None
    sma50: float | None = None

    def leader_detail(self) -> str:
        passed = "n/a" if self.tt_passed is None else f"{self.tt_passed}/8"
        rs = "n/a" if self.rs_rating is None else str(self.rs_rating)
        return f"Trend Template {passed}, RS Rating {rs}."

    def as_dict(self) -> dict[str, Any]:
        return {
            "tt_passed": self.tt_passed,
            "stage": self.stage,
            "rs_rating": self.rs_rating,
            "rs_line_high_52w": self.rs_line_high_52w,
            "rs_new_high_ahead": self.rs_new_high_ahead,
            "up_down_volume": self.up_down_volume,
            "volume_ratio": self.volume_ratio,
            "ema21": self.ema21,
            "sma50": self.sma50,
        }


@dataclass(frozen=True)
class Tracked:
    """The pattern a setup follows, from today's detection or the stored pattern row."""

    id: int | None
    type: str
    start: date
    pivot: float
    base_low: float | None
    quality: float
    base_number: int | None = None
    logical_low: float | None = None  # the last swing low: final contraction, handle, flag
    lows: tuple[tuple[date, float], ...] = ()  # the base's swing lows, for undercut & rally
    breakout_date: date | None = None
    status: str = Status.FORMING

    @property
    def label(self) -> str:
        try:
            return LABELS[PatternType(self.type)]
        except ValueError:
            return self.type

    @property
    def noun(self) -> str:
        """The label inside a sentence: "cup with handle", "volatility contraction (VCP)"."""
        return self.label[:1].lower() + self.label[1:]

    @property
    def is_gap(self) -> bool:
        return self.type == PatternType.EARNINGS_GAP

    @classmethod
    def from_match(cls, pattern_id: int | None, m: PatternMatch) -> "Tracked":
        return cls.from_parts(
            pattern_id,
            str(m.type),
            m.start,
            m.pivot,
            m.base_low,
            m.quality,
            m.base_number,
            [(p.date, p.price, p.kind) for p in m.swings],
            m.details.get("breakout_date"),
            str(m.status),
        )

    @classmethod
    def from_parts(
        cls,
        pattern_id: int | None,
        type_: str,
        start: date,
        pivot: float,
        base_low: float | None,
        quality: float,
        base_number: int | None,
        swings: Sequence[tuple[date, float, str]],
        breakout_date: str | date | None,
        status: str,
    ) -> "Tracked":
        lows = tuple((d, float(p)) for d, p, kind in swings if kind == "low")
        logical = lows[-1][1] if lows else base_low
        breakout = date.fromisoformat(breakout_date) if isinstance(breakout_date, str) else None
        if isinstance(breakout_date, date):
            breakout = breakout_date
        return cls(
            pattern_id,
            type_,
            start,
            float(pivot),
            None if base_low is None else float(base_low),
            float(quality),
            base_number,
            logical,
            lows,
            breakout,
            status,
        )


@dataclass(frozen=True)
class Detected:
    """A detection as of today, with its `patterns` row id."""

    id: int | None
    match: PatternMatch


@dataclass(frozen=True)
class StockDay:
    ticker_id: int
    bars: Bars  # through the as-of session
    tech: Technicals
    fundamentals_grade: str | None = None
    insider_cluster: bool = False
    group_name: str | None = None
    group_rank: int | None = None
    groups_ranked: int | None = None
    next_earnings: date | None = None
    sessions_to_earnings: int | None = None
    pocket_pivots_recent: int = 0
    detected: tuple[Detected, ...] = ()
    spent: frozenset[int] = frozenset()  # pattern ids whose setups already ended


@dataclass(frozen=True)
class MarketDay:
    state: str | None = None
    label: str | None = None


@dataclass
class SetupRecord:
    """A working copy of a `setups` row."""

    ticker_id: int
    kind: str  # watch | base | episodic_pivot
    state: State
    state_since: date
    first_seen: date
    as_of: date
    close: float
    id: int | None = None
    pattern: Tracked | None = None
    pivot: float | None = None
    base_low: float | None = None
    trade_plan: dict[str, Any] | None = None
    breakout_date: date | None = None
    best_grade: str | None = None
    active: bool = True
    closed_on: date | None = None
    closed_reason: str | None = None
    score: SetupScore | None = None
    readiness: float | None = None


@dataclass(frozen=True)
class TransitionDraft:
    date: date
    from_state: str | None
    to_state: str
    reason: str


@dataclass(frozen=True)
class SignalDraft:
    type: str
    summary: str
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass
class SetupWrite:
    record: SetupRecord
    transitions: list[TransitionDraft] = field(default_factory=list)
    signals: list[SignalDraft] = field(default_factory=list)
    today: Transition | None = None  # the as-of session's lifecycle result


@dataclass
class StockResult:
    writes: list[SetupWrite] = field(default_factory=list)
    signals: list[SignalDraft] = field(default_factory=list)  # not tied to a setup


def better_grade(a: str | None, b: str | None) -> str | None:
    return a if GRADE_ORDER.get(a or "", 0) >= GRADE_ORDER.get(b or "", 0) else b


def evaluate_stock(
    stock: StockDay,
    active: SetupRecord | None,
    market: MarketDay,
    settings: AppSettings,
    as_of: date,
) -> StockResult:
    return _Evaluator(stock, market, settings, as_of).run(active)


class _Evaluator:
    def __init__(
        self, stock: StockDay, market: MarketDay, settings: AppSettings, as_of: date
    ) -> None:
        bars = stock.bars
        if not len(bars) or bars.dates[-1] != as_of:
            raise ValueError(f"ticker {stock.ticker_id}: no bar on {as_of}")
        self.stock, self.market, self.s, self.as_of = stock, market, settings, as_of
        self.bars, self.t = bars, bars.last
        self.by_id = {d.id: d for d in stock.detected if d.id is not None}
        self.spent = set(stock.spent)
        bases, gaps = [], []
        for d in stock.detected:
            m = d.match
            if d.id is not None and d.id in self.spent:
                continue
            if m.type not in EVENTS and m.status in CURRENT:
                bases.append(Tracked.from_match(d.id, m))
            elif m.type == PatternType.EARNINGS_GAP and m.status == Status.FORMING:
                gaps.append(Tracked.from_match(d.id, m))
        bases.sort(key=lambda p: -p.quality)
        gaps.sort(key=lambda p: -p.quality)
        self.candidates = bases + gaps

    # --- Orchestration ---------------------------------------------------------------------

    def run(self, active: SetupRecord | None) -> StockResult:
        result = StockResult()
        current = active
        if current is not None:
            write = self._advance(current)
            result.writes.append(write)
            if not current.active:
                if current.pattern is not None and current.pattern.id is not None:
                    self.spent.add(current.pattern.id)
                current = None
        if current is None:
            opened = self._open()
            if opened is not None:
                result.writes.append(opened)
        for write in result.writes:
            self._finish(write)
            self._lifecycle_signals(write)
        self._stock_signals(result)
        return result

    def _day(self, i: int) -> Session:
        b = self.bars
        average = float(b.avg_volume_50[i])
        return Session(
            b.dates[i],
            close=float(b.close[i]),
            high=float(b.high[i]),
            low=float(b.low[i]),
            volume=float(b.volume[i]),
            avg_volume_50=average if np.isfinite(average) and average > 0 else None,
        )

    def _index(self, day: date) -> int | None:
        dates = self.bars.dates
        i = int(np.searchsorted(self.bars.days, np.datetime64(day)))
        return i if i < len(dates) and dates[i] == day else None

    def _since_breakout(self, rec: SetupRecord, i: int) -> int | None:
        if rec.breakout_date is None:
            return None
        start = self._index(rec.breakout_date)
        return FAR_PAST if start is None else i - start

    def _plan(self, rec: SetupRecord, i: int) -> dict[str, Any] | None:
        if rec.pivot is None:
            return None
        low = rec.pattern.logical_low if rec.pattern else None
        low = low if low is not None else rec.base_low
        if low is None:
            return None
        b = self.bars
        ema21, sma50 = float(b.ema21[i]), float(b.sma50[i])
        return build_trade_plan(
            pivot=rec.pivot,
            logical_low=low,
            settings=self.s,
            ema21=ema21 if np.isfinite(ema21) else None,
            sma50=sma50 if np.isfinite(sma50) else None,
        ).as_dict()

    def _stop(self, rec: SetupRecord) -> float | None:
        return None if rec.trade_plan is None else float(rec.trade_plan["stop"])

    # --- Lifecycle -------------------------------------------------------------------------

    def _apply(
        self,
        rec: SetupRecord,
        write: SetupWrite,
        tr: Transition,
        i: int,
        *,
        from_state: State | None,
        force_log: bool = False,
        prefix: str = "",
    ) -> None:
        day = self.bars.dates[i]
        if force_log or from_state != tr.state:
            write.transitions.append(
                TransitionDraft(
                    day,
                    None if from_state is None else str(from_state),
                    str(tr.state),
                    prefix + tr.reason,
                )
            )
            rec.state_since = day
        rec.state = tr.state
        if tr.breakout_today:
            rec.breakout_date = day
            rec.trade_plan = self._plan(rec, i)  # frozen from here on
        if tr.state in TERMINAL or tr.ended:
            rec.active = False
            rec.closed_on = day
            rec.closed_reason = tr.reason
        if i == self.t:
            write.today = tr

    def _step(
        self, rec: SetupRecord, i: int, *, first: bool, current: bool, failed: bool
    ) -> Transition:
        facts = SetupFacts(
            state=None if first else rec.state,
            kind=rec.kind,
            pivot=rec.pivot,
            base_low=rec.base_low,
            pattern_current=current,
            pattern_failed=failed,
            stop=self._stop(rec),
            sessions_since_breakout=self._since_breakout(rec, i),
            trend_leader=self.stock.tech.tt_pass,
            leader_detail=self.stock.tech.leader_detail(),
            sma50=_finite(self.bars.sma50[i]),
        )
        return next_state(facts, self._day(i), self.s)

    def _track(
        self, rec: SetupRecord, pattern: Tracked, write: SetupWrite, *, from_state: State | None
    ) -> None:
        """Follow `pattern` from now on (a new setup, a watch converting or a switch). A pattern
        that broke out (or gapped) before today is replayed from that session."""
        switching = from_state is not None
        rec.pattern = pattern
        rec.kind = "episodic_pivot" if pattern.is_gap else "base"
        rec.pivot, rec.base_low = pattern.pivot, pattern.base_low
        rec.breakout_date, rec.trade_plan = None, None
        start = self.t
        replay_from = pattern.start if pattern.is_gap else pattern.breakout_date
        if replay_from is not None and replay_from < self.as_of:
            found = self._index(replay_from)
            start = found if found is not None else self.t
        verb = "Switched to" if from_state in PRE_BREAKOUT else "Tracking"
        prefix = f"{verb} {_article(pattern.noun)} (pivot {pattern.pivot:.2f}). "
        if not switching:
            prefix = f"New {pattern.noun} setup (pivot {pattern.pivot:.2f}). "
        for i in range(start, self.t + 1):
            first = i == start
            tr = self._step(rec, i, first=first, current=True, failed=False)
            self._apply(
                rec,
                write,
                tr,
                i,
                from_state=from_state if first else rec.state,
                force_log=first,
                prefix=prefix if first else "",
            )
            if not rec.active:
                break

    def _advance(self, rec: SetupRecord) -> SetupWrite:
        write = SetupWrite(rec)
        t = self.t
        if rec.kind == "watch":
            if self.candidates:
                self._track(rec, self.candidates[0], write, from_state=rec.state)
                return write
            tr = self._step(rec, t, first=False, current=False, failed=False)
            self._apply(rec, write, tr, t, from_state=rec.state)
            return write

        if rec.state in PRE_BREAKOUT:
            seen = self.by_id.get(rec.pattern.id) if rec.pattern and rec.pattern.id else None
            current = seen is not None and seen.match.status in CURRENT
            failed = seen is not None and seen.match.status == Status.FAILED
            others = [c for c in self.candidates if rec.pattern is None or c.id != rec.pattern.id]
            if not current and not failed and others:
                self._track(rec, others[0], write, from_state=rec.state)
                return write
            if current and seen is not None:
                rec.pattern = Tracked.from_match(seen.id, seen.match)
                rec.pivot, rec.base_low = rec.pattern.pivot, rec.pattern.base_low
            tr = self._step(rec, t, first=False, current=current, failed=failed)
            self._apply(rec, write, tr, t, from_state=rec.state)
            return write

        tr = self._step(rec, t, first=False, current=True, failed=False)
        self._apply(rec, write, tr, t, from_state=rec.state)
        if rec.active and rec.breakout_date is not None:
            breakout = rec.breakout_date
            fresh = [
                c
                for c in self.candidates
                if c.start > breakout and c.status == Status.FORMING and not c.is_gap
            ]
            if fresh:
                rec.active = False
                rec.closed_on = self.as_of
                rec.closed_reason = (
                    f"Superseded by a new {fresh[0].noun} (pivot {fresh[0].pivot:.2f})."
                )
        return write

    def _open(self) -> SetupWrite | None:
        for pattern in self.candidates:
            if pattern.id is not None and pattern.id in self.spent:
                continue
            rec = SetupRecord(
                ticker_id=self.stock.ticker_id,
                kind="base",
                state=State.BASING,
                state_since=self.as_of,
                first_seen=self.as_of,
                as_of=self.as_of,
                close=float(self.bars.close[self.t]),
            )
            write = SetupWrite(rec)
            self._track(rec, pattern, write, from_state=None)
            if rec.active:
                return write
        if self.stock.tech.tt_pass:
            rec = SetupRecord(
                ticker_id=self.stock.ticker_id,
                kind="watch",
                state=State.WATCH,
                state_since=self.as_of,
                first_seen=self.as_of,
                as_of=self.as_of,
                close=float(self.bars.close[self.t]),
            )
            write = SetupWrite(rec)
            tr = self._step(rec, self.t, first=True, current=False, failed=False)
            self._apply(rec, write, tr, self.t, from_state=None, force_log=True)
            return write
        return None

    # --- Score, plan, signals -------------------------------------------------------------

    def _finish(self, write: SetupWrite) -> None:
        rec = write.record
        tech, t = self.stock.tech, self.t
        rec.as_of = self.as_of
        rec.close = float(self.bars.close[t])
        if rec.state in PRE_BREAKOUT and rec.active:
            rec.trade_plan = self._plan(rec, t)
        pattern = rec.pattern
        inputs = ScoreInputs(
            tt_passed=tech.tt_passed,
            stage=tech.stage,
            rs_rating=tech.rs_rating,
            rs_line_high_52w=tech.rs_line_high_52w,
            rs_new_high_ahead=tech.rs_new_high_ahead,
            rs_slope_63=tech.rs_slope_63,
            fundamentals_grade=self.stock.fundamentals_grade,
            pattern_quality=None if pattern is None else pattern.quality,
            pattern_label=None if pattern is None else pattern.label,
            group_name=self.stock.group_name,
            group_rank=self.stock.group_rank,
            groups_ranked=self.stock.groups_ranked,
            up_down_volume=tech.up_down_volume,
            pocket_pivots_recent=self.stock.pocket_pivots_recent,
            insider_cluster=self.stock.insider_cluster,
            regime=self.market.state,
            regime_label=self.market.label,
        )
        flags = find_red_flags(
            self.bars,
            self.s,
            pivot=rec.pivot,
            base_start=None if pattern is None or pattern.is_gap else pattern.start,
            base_number=None if pattern is None else pattern.base_number,
            next_earnings=self.stock.next_earnings,
            sessions_to_earnings=self.stock.sessions_to_earnings,
        )
        previous_best = rec.best_grade
        rec.score = score_setup(inputs, self.s, flags)
        rec.readiness = readiness_pct(rec.close, rec.pivot)
        rec.best_grade = better_grade(previous_best, rec.score.grade)
        grade = rec.score.grade
        if (
            rec.active
            and rec.pivot is not None
            and rec.state in (State.BASING, State.NEAR_PIVOT, State.BREAKOUT)
            and grade in TOP_GRADES
            and previous_best not in TOP_GRADES
        ):
            label = pattern.noun if pattern else "setup"
            below = "" if rec.readiness is None else f", close {rec.readiness:.1f}% below the pivot"
            if rec.readiness is not None and rec.readiness < 0:
                below = f", close {-rec.readiness:.1f}% above the pivot"
            write.signals.append(
                SignalDraft(
                    "new_top_setup",
                    f"New {grade} setup: {label} scoring {rec.score.final:.0f}/100 (pivot "
                    f"{rec.pivot:.2f}{below}).",
                )
            )

    def _lifecycle_signals(self, write: SetupWrite) -> None:
        rec, tr = write.record, write.today
        # The reason logged for each stage entered today (with "New … setup" when it's new).
        entered = {
            x.to_state: x.reason
            for x in write.transitions
            if x.date == self.as_of and x.from_state != x.to_state
        }
        if tr is not None and tr.breakout_today:
            reason = entered.get(str(tr.state), tr.reason)
            write.signals.append(SignalDraft("breakout", f"Breakout confirmed: {reason}"))
        elif State.EXTENDED in entered:
            write.signals.append(SignalDraft("extended", f"Extended: {entered[State.EXTENDED]}"))
        fresh_cross = (
            rec.pivot is not None and self.t > 0 and self.bars.close[self.t - 1] <= rec.pivot
        )
        if tr is not None and tr.rejected_breakout and fresh_cross:
            write.signals.append(
                SignalDraft("breakout_rejected", f"Breakout rejected: {tr.rejected_breakout}")
            )
        for state, kind, label in (
            (State.NEAR_PIVOT, "near_pivot", "Near pivot"),
            (State.FAILED, "failed", "Breakout failed"),
            (State.INVALIDATED, "invalidated", "Setup invalidated"),
        ):
            if state in entered and not (state == State.INVALIDATED and rec.kind == "watch"):
                write.signals.append(SignalDraft(kind, f"{label}: {entered[state]}"))

    def _stock_signals(self, result: StockResult) -> None:
        active = next((w for w in result.writes if w.record.active), None)
        target = active.signals if active is not None else result.signals
        tech = self.stock.tech
        for d in self.stock.detected:
            m = d.match
            if m.type == PatternType.POCKET_PIVOT and m.end == self.as_of:
                ratio = m.details.get("volume_vs_down_max")
                target.append(
                    SignalDraft(
                        "pocket_pivot",
                        f"Pocket pivot: volume {ratio}× the largest down day of the previous "
                        f"{self.s.pocket_pivot_lookback_days} sessions, quality "
                        f"{m.quality:.0f}/100.",
                        {"pattern_id": d.id},
                    )
                )
            if m.type == PatternType.EARNINGS_GAP and m.start == self.as_of:
                target.append(
                    SignalDraft(
                        "earnings_gap",
                        f"Earnings gap: opened {m.details.get('gap_pct')}% above the previous "
                        f"close on {m.details.get('volume_multiple')}× average volume.",
                        {"pattern_id": d.id},
                    )
                )
        if active is None:
            return
        rec = active.record
        if tech.rs_new_high_ahead and not tech.rs_new_high_ahead_before:
            target.append(
                SignalDraft(
                    "rs_new_high_ahead",
                    "RS line at a new 52-week high ahead of price: the stock is outperforming "
                    "the market before its own breakout.",
                )
            )
        trigger: Trigger | None = None
        if rec.state in POST_BREAKOUT and rec.breakout_date is not None:
            index = self._index(rec.breakout_date)
            trigger = pullback_to_average(
                self.bars, self.s, rs_rating=tech.rs_rating, breakout_index=index
            )
        elif rec.state in PRE_BREAKOUT and rec.kind == "base" and rec.pattern is not None:
            trigger = undercut_and_rally(self.bars, self.s, lows=rec.pattern.lows)
        if trigger is not None:
            label = "Pullback buy point" if trigger.type == "pullback" else "Undercut & rally"
            target.append(
                SignalDraft(trigger.type, f"{label}: {trigger.detail}", {"level": trigger.level})
            )


def _article(noun: str) -> str:
    return ("an " if noun[:1] in "aeiou" else "a ") + noun


def _finite(value: float) -> float | None:
    v = float(value)
    return v if np.isfinite(v) else None


__all__ = [
    "SIGNAL_LABELS",
    "Detected",
    "MarketDay",
    "SetupRecord",
    "SetupWrite",
    "SignalDraft",
    "StockDay",
    "StockResult",
    "Technicals",
    "Tracked",
    "TransitionDraft",
    "better_grade",
    "evaluate_stock",
]
