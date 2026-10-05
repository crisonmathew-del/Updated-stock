"""Fundamentals Grade A-E (spec §6.5), computed as of a date from point-in-time statements.

Each component earns points with the numbers behind them, so the grade is a readable sum:

EPS path (latest quarter and trailing-year EPS positive), points from settings.grade_weights:
- eps_growth: latest EPS vs the same period a year earlier. Below `eps_growth_q_min` earns
  nothing; at the minimum half the points, rising linearly to full at `eps_growth_q_strong`.
  Growth from a loss (or zero) to a profit is a "turnaround": half the points, no percentage.
- eps_acceleration: year-over-year EPS growth rising over the last three periods earns full
  points; rising over the last two, half.
- sales_growth: at least `sales_growth_q_min` earns two thirds; the rest if it accelerated.
- annual_eps_growth: average yearly EPS growth over up to three fiscal years, scored like
  eps_growth against `eps_growth_annual_min` / `eps_growth_annual_strong`.
- roe: trailing-year net income / average equity at least `roe_min`.
- margins: operating and net margin each higher than a year earlier (shared equally).
- accumulation: 50-day up/down volume ratio at least `accumulation_up_down_ratio_min` earns
  full points, at least 1.0 half.
- insider_bonus: `insider_cluster_min_insiders` or more officers/directors buying in the open
  market within `insider_cluster_window_days` (by filing date) adds bonus points.

Revenue-led path (no positive EPS), settings.revenue_grade_weights: sales_growth (scored like
eps_growth against sales_growth_q_min / sales_growth_q_strong), sales_acceleration, margins
(trend, so a narrowing loss counts as improving), accumulation; plus the insider bonus.

Score = 100 × points earned / points available among components with data, plus the bonus,
capped at 100. With less than `grade_min_coverage_pct` of the points covered there is no grade.

Point in time: per period, the latest version reported on or before `as_of`. EPS reported
before a split is divided by every split ratio with ex-date after the report and on or before
`as_of`. Companies with recent quarterly data use quarters; foreign filers that report
annually (20-F) use fiscal years for every component ("annual" basis).
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from itertools import pairwise

from app.settings.schema import AppSettings

YEAR_AGO = range(357, 372)
PREVIOUS_QUARTER = range(80, 101)
PREVIOUS_YEAR = range(357, 372)
TRAILING_YEAR_SPAN = range(255, 295)  # end of the oldest of four quarters → end of the newest
RECENT_QUARTER = timedelta(days=200)


@dataclass(frozen=True, slots=True)
class StatementRow:
    """One stored version of a period (see app.models.fundamentals)."""

    period_end: date
    reported_date: date
    fiscal_year: int | None = None
    fiscal_period: str | None = None
    eps: float | None = None  # diluted, falling back to basic
    revenue: float | None = None
    net_income: float | None = None
    operating_income: float | None = None
    equity: float | None = None
    derived: bool = False
    currency: str | None = "USD"


@dataclass(frozen=True, slots=True)
class InsiderTrade:
    transaction_date: date
    filed_date: date
    insider_cik: str
    insider_name: str
    code: str  # P = purchase, S = sale
    is_director: bool
    is_officer: bool


@dataclass(frozen=True, slots=True)
class Split:
    ex_date: date
    ratio: float  # new shares per old share


@dataclass
class Component:
    key: str
    label: str
    max_points: float
    points: float
    status: str  # pass | partial | fail | no_data
    detail: str
    bonus: bool = False

    def as_dict(self) -> dict[str, object]:
        return {
            "key": self.key,
            "label": self.label,
            "points": round(self.points, 2),
            "max_points": self.max_points,
            "status": self.status,
            "detail": self.detail,
            "bonus": self.bonus,
        }


@dataclass
class GradeResult:
    grade: str | None
    score: float | None
    path: str  # eps | revenue
    basis: str  # quarterly | annual | none
    coverage_pct: float
    components: list[Component] = field(default_factory=list)

    def components_json(self) -> list[dict[str, object]]:
        return [c.as_dict() for c in self.components]


# --- Formatting -----------------------------------------------------------------------------


def _money(value: float | None, currency: str | None) -> str:
    if value is None:
        return "n/a"
    symbol = "$" if currency in (None, "USD") else ""
    suffix = "" if symbol else f" {currency}"
    size = abs(value)
    sign = "-" if value < 0 else ""
    if size >= 1e9:
        text = f"{size / 1e9:.2f}B"
    elif size >= 1e6:
        text = f"{size / 1e6:.1f}M"
    else:
        text = f"{size:,.0f}"
    return f"{sign}{symbol}{text}{suffix}"


def _per_share(value: float | None, currency: str | None) -> str:
    if value is None:
        return "n/a"
    symbol = "$" if currency in (None, "USD") else ""
    suffix = "" if symbol else f" {currency}"
    sign = "-" if value < 0 else ""
    return f"{sign}{symbol}{abs(value):.2f}{suffix}"


def _pct(value: float) -> str:
    return f"{value:+.1f}%"


def _label(period: StatementRow, annual: bool) -> str:
    if period.fiscal_year is not None and period.fiscal_period is not None:
        return (
            f"FY{period.fiscal_year}"
            if annual
            else f"{period.fiscal_period} FY{period.fiscal_year}"
        )
    kind = "year" if annual else "quarter"
    return f"{kind} ended {period.period_end.isoformat()}"


# --- Point-in-time series -------------------------------------------------------------------


def as_known(
    rows: Iterable[StatementRow], as_of: date, splits: Sequence[Split]
) -> list[StatementRow]:
    """Per period, the latest version reported by `as_of`, EPS adjusted for later splits."""
    latest: dict[date, StatementRow] = {}
    for row in rows:
        if row.reported_date > as_of:
            continue
        current = latest.get(row.period_end)
        if current is None or row.reported_date >= current.reported_date:
            latest[row.period_end] = row
    out = []
    for row in sorted(latest.values(), key=lambda r: r.period_end):
        factor = 1.0
        for split in splits:
            if row.reported_date < split.ex_date <= as_of and split.ratio > 0:
                factor *= split.ratio
        if factor != 1.0 and row.eps is not None:
            row = StatementRow(
                period_end=row.period_end,
                reported_date=row.reported_date,
                fiscal_year=row.fiscal_year,
                fiscal_period=row.fiscal_period,
                eps=row.eps / factor,
                revenue=row.revenue,
                net_income=row.net_income,
                operating_income=row.operating_income,
                equity=row.equity,
                derived=row.derived,
                currency=row.currency,
            )
        out.append(row)
    return out


def find_period(periods: Sequence[StatementRow], end: date, gap: range) -> StatementRow | None:
    """The latest period ending `gap` days before `end` (e.g. YEAR_AGO for the same quarter a
    year earlier)."""
    for p in reversed(periods):
        if (end - p.period_end).days in gap:
            return p
    return None


def _chain(periods: Sequence[StatementRow], count: int, gap: range) -> list[StatementRow]:
    """The latest period and up to `count - 1` consecutive predecessors, newest first."""
    if not periods:
        return []
    chain = [periods[-1]]
    while len(chain) < count:
        previous = find_period(periods, chain[-1].period_end, gap)
        if previous is None:
            break
        chain.append(previous)
    return chain


@dataclass(frozen=True)
class Growth:
    value: float | None  # percent; None when not meaningful
    kind: str  # growth | turnaround | loss | missing
    current: float | None
    prior: float | None


def growth(current: float | None, prior: float | None) -> Growth:
    if current is None or prior is None:
        return Growth(None, "missing", current, prior)
    if prior > 0:
        return Growth((current - prior) / prior * 100, "growth", current, prior)
    if current > 0:
        return Growth(None, "turnaround", current, prior)
    return Growth(None, "loss", current, prior)


def _ramp(value: float, minimum: float, strong: float) -> float:
    """0 below the minimum, 0.5 at it, rising linearly to 1 at `strong`."""
    if value < minimum:
        return 0.0
    if strong <= minimum:
        return 1.0
    return 0.5 + 0.5 * min(1.0, (value - minimum) / (strong - minimum))


def _status(points: float, max_points: float) -> str:
    if points >= max_points - 1e-9:
        return "pass"
    return "partial" if points > 0 else "fail"


# --- Components -----------------------------------------------------------------------------


@dataclass
class _Context:
    periods: list[StatementRow]  # quarters or years, oldest first
    annual_periods: list[StatementRow]
    annual_basis: bool
    as_of: date
    settings: AppSettings

    @property
    def previous_gap(self) -> range:
        return PREVIOUS_YEAR if self.annual_basis else PREVIOUS_QUARTER

    def latest(self, count: int) -> list[StatementRow]:
        return _chain(self.periods, count, self.previous_gap)

    def year_ago(self, period: StatementRow) -> StatementRow | None:
        return find_period(self.periods, period.period_end, YEAR_AGO)

    def growth_of(self, period: StatementRow, attr: str) -> Growth:
        prior = self.year_ago(period)
        return growth(getattr(period, attr), getattr(prior, attr) if prior else None)

    def label(self, period: StatementRow) -> str:
        return _label(period, self.annual_basis)

    @property
    def currency(self) -> str | None:
        return self.periods[-1].currency if self.periods else None


def _scaled_growth(
    ctx: _Context,
    key: str,
    label: str,
    weight: float,
    attr: str,
    noun: str,
    minimum: float,
    strong: float,
) -> Component:
    latest = ctx.latest(1)
    if not latest:
        return Component(key, label, weight, 0, "no_data", f"No {noun} reported yet.")
    period = latest[0]
    g = ctx.growth_of(period, attr)
    fmt = _per_share if attr == "eps" else _money
    numbers = (
        f"{ctx.label(period)} {noun} {fmt(g.current, ctx.currency)} "
        f"vs {fmt(g.prior, ctx.currency)} a year earlier"
    )
    if g.kind == "missing":
        return Component(key, label, weight, 0, "no_data", f"No year-earlier {noun} to compare.")
    if g.kind == "turnaround":
        return Component(
            key, label, weight, weight / 2, "partial", f"{numbers}: turnaround from a loss."
        )
    if g.kind == "loss":
        return Component(key, label, weight, 0, "fail", f"{numbers}: still a loss.")
    assert g.value is not None
    points = weight * _ramp(g.value, minimum, strong)
    need = f"needs {minimum:g}%, full credit at {strong:g}%"
    return Component(
        key, label, weight, points, _status(points, weight), f"{numbers}: {_pct(g.value)} ({need})."
    )


def _acceleration(
    ctx: _Context, key: str, label: str, weight: float, attr: str, noun: str
) -> Component:
    rates: list[float] = []
    for period in ctx.latest(3):
        g = ctx.growth_of(period, attr)
        if g.value is None:
            break
        rates.append(g.value)
    if len(rates) < 2:
        return Component(
            key, label, weight, 0, "no_data", f"Needs year-over-year {noun} growth for 2+ periods."
        )
    oldest_first = rates[::-1]
    trail = " → ".join(_pct(r) for r in oldest_first)
    if len(rates) == 3 and rates[0] > rates[1] > rates[2]:
        return Component(
            key, label, weight, weight, "pass", f"{noun.capitalize()} growth {trail}: accelerating."
        )
    if rates[0] > rates[1]:
        what = "up from the period before" if len(rates) == 3 else "up (only 2 periods known)"
        return Component(
            key,
            label,
            weight,
            weight / 2,
            "partial",
            f"{noun.capitalize()} growth {trail}: {what}.",
        )
    return Component(
        key, label, weight, 0, "fail", f"{noun.capitalize()} growth {trail}: not accelerating."
    )


def _sales_growth_eps_path(ctx: _Context, weight: float) -> Component:
    key, label = "sales_growth", "Sales growth"
    latest = ctx.latest(2)
    if not latest:
        return Component(key, label, weight, 0, "no_data", "No sales reported yet.")
    g0 = ctx.growth_of(latest[0], "revenue")
    if g0.value is None:
        return Component(key, label, weight, 0, "no_data", "No year-earlier sales to compare.")
    minimum = ctx.settings.sales_growth_q_min
    numbers = (
        f"{ctx.label(latest[0])} sales {_money(g0.current, ctx.currency)} vs "
        f"{_money(g0.prior, ctx.currency)} a year earlier: {_pct(g0.value)}"
    )
    if g0.value < minimum:
        return Component(key, label, weight, 0, "fail", f"{numbers} (needs {minimum:g}%).")
    g1 = ctx.growth_of(latest[1], "revenue") if len(latest) > 1 else None
    if g1 is not None and g1.value is not None and g0.value > g1.value:
        return Component(
            key, label, weight, weight, "pass", f"{numbers}, accelerating from {_pct(g1.value)}."
        )
    after = f", not faster than {_pct(g1.value)} before" if g1 and g1.value is not None else ""
    return Component(key, label, weight, weight * 2 / 3, "partial", f"{numbers}{after}.")


def _annual_eps_growth(ctx: _Context, weight: float) -> Component:
    key, label = "annual_eps_growth", "Annual EPS growth (3 years)"
    years = _chain(ctx.annual_periods, 4, PREVIOUS_YEAR)[::-1]
    rates = [
        g.value
        for older, newer in pairwise(years)
        if (g := growth(newer.eps, older.eps)).value is not None
    ]
    if not rates:
        return Component(
            key,
            label,
            weight,
            0,
            "no_data",
            "Needs EPS for 2+ consecutive fiscal years with a profit.",
        )
    average = sum(rates) / len(rates)
    s = ctx.settings
    points = weight * _ramp(average, s.eps_growth_annual_min, s.eps_growth_annual_strong)
    currency = years[-1].currency
    trail = " → ".join(_per_share(y.eps, currency) for y in years)
    span = f"{_label(years[0], True)}-{_label(years[-1], True)}"
    return Component(
        key,
        label,
        weight,
        points,
        _status(points, weight),
        f"EPS {span}: {trail}; average growth {_pct(average)} over {len(rates)} year(s) "
        f"(needs {s.eps_growth_annual_min:g}%, full credit at {s.eps_growth_annual_strong:g}%).",
    )


def _roe(ctx: _Context, weight: float) -> Component:
    key, label = "roe", "Return on equity"
    minimum = ctx.settings.roe_min
    if ctx.annual_basis:
        latest = ctx.latest(1)
        if not latest or latest[0].net_income is None or latest[0].equity is None:
            return Component(key, label, weight, 0, "no_data", "Needs net income and equity.")
        income, period = latest[0].net_income, latest[0]
        prior = ctx.year_ago(period)
        what = f"{ctx.label(period)} net income"
    else:
        quarters = ctx.latest(4)
        if (
            len(quarters) < 4
            or any(q.net_income is None for q in quarters)
            or quarters[0].equity is None
            or (quarters[0].period_end - quarters[-1].period_end).days not in TRAILING_YEAR_SPAN
        ):
            return Component(
                key,
                label,
                weight,
                0,
                "no_data",
                "Needs four consecutive quarters of net income and equity.",
            )
        income = sum(q.net_income or 0 for q in quarters)
        period = quarters[0]
        prior = ctx.year_ago(period)
        what = "Trailing 4 quarters' net income"
    assert period.equity is not None
    equities = [period.equity] + ([prior.equity] if prior and prior.equity is not None else [])
    average = sum(equities) / len(equities)
    if average <= 0:
        return Component(
            key,
            label,
            weight,
            0,
            "no_data",
            "Shareholders' equity is negative: ROE is not meaningful.",
        )
    roe = income / average * 100
    basis = "average equity" if len(equities) == 2 else "equity"
    detail = (
        f"{what} {_money(income, ctx.currency)} ÷ {basis} {_money(average, ctx.currency)} "
        f"= {roe:.1f}% (needs {minimum:g}%)."
    )
    points = weight if roe >= minimum else 0.0
    return Component(key, label, weight, points, _status(points, weight), detail)


def _margins(ctx: _Context, weight: float, label: str) -> Component:
    key = "margins"
    latest = ctx.latest(1)
    if not latest:
        return Component(key, label, weight, 0, "no_data", "No margins reported yet.")
    period, prior = latest[0], ctx.year_ago(latest[0])
    parts: list[str] = []
    improved = 0
    known = 0
    for name, attr in (("Operating margin", "operating_income"), ("Net margin", "net_income")):
        now = _margin(getattr(period, attr), period.revenue)
        then = _margin(getattr(prior, attr), prior.revenue) if prior else None
        if now is None or then is None:
            continue
        known += 1
        up = now > then
        improved += up
        parts.append(
            f"{name} {now:.1f}% vs {then:.1f}% a year earlier ({'up' if up else 'not up'})"
        )
    if not known:
        return Component(
            key,
            label,
            weight,
            0,
            "no_data",
            "Needs revenue and income for this and the year-earlier period.",
        )
    points = weight * improved / known
    return Component(key, label, weight, points, _status(points, weight), "; ".join(parts) + ".")


def _margin(income: float | None, revenue: float | None) -> float | None:
    if income is None or revenue is None or revenue <= 0:
        return None
    return income / revenue * 100


def _accumulation(settings: AppSettings, ratio: float | None, weight: float) -> Component:
    key, label = "accumulation", "Accumulation (up/down volume)"
    minimum = settings.accumulation_up_down_ratio_min
    if ratio is None:
        return Component(key, label, weight, 0, "no_data", "Needs 50 sessions of price history.")
    points = weight if ratio >= minimum else weight / 2 if ratio >= 1.0 else 0.0
    return Component(
        key,
        label,
        weight,
        points,
        _status(points, weight),
        f"50-day up/down volume ratio {ratio:.2f} (accumulation at {minimum:g}, neutral at 1.0).",
    )


def _insider_bonus(
    settings: AppSettings, trades: Sequence[InsiderTrade], as_of: date, bonus: float
) -> Component:
    key, label = "insider_bonus", "Insider cluster buying"
    window = settings.insider_cluster_window_days
    start = as_of - timedelta(days=window)
    buyers: dict[str, str] = {}
    for t in trades:
        if (
            t.code == "P"
            and t.filed_date <= as_of
            and start < t.transaction_date <= as_of
            and (t.is_director or t.is_officer)
        ):
            buyers.setdefault(t.insider_cik, t.insider_name)
    needed = settings.insider_cluster_min_insiders
    names = ", ".join(sorted(buyers.values()))
    if len(buyers) >= needed:
        return Component(
            key,
            label,
            bonus,
            bonus,
            "pass",
            f"{len(buyers)} officers/directors bought in the open market in the last "
            f"{window} days: {names}.",
            bonus=True,
        )
    seen = f" ({names})" if names else ""
    return Component(
        key,
        label,
        bonus,
        0,
        "fail",
        f"{len(buyers)} officer/director open-market buyer(s) in the last {window} days{seen}; "
        f"a cluster needs {needed}.",
        bonus=True,
    )


# --- Grade ----------------------------------------------------------------------------------


def _letter(score: float, settings: AppSettings) -> str:
    c = settings.grade_cutoffs
    for letter, cutoff in (("A", c.a), ("B", c.b), ("C", c.c), ("D", c.d)):
        if score >= cutoff:
            return letter
    return "E"


def grade_fundamentals(
    quarterly: Iterable[StatementRow],
    annual: Iterable[StatementRow],
    *,
    as_of: date,
    settings: AppSettings,
    up_down_volume: float | None = None,
    insider_trades: Sequence[InsiderTrade] = (),
    splits: Sequence[Split] = (),
) -> GradeResult:
    quarters = as_known(quarterly, as_of, splits)
    years = as_known(annual, as_of, splits)
    recent_quarters = (
        bool(quarters)
        and as_of - quarters[-1].period_end <= RECENT_QUARTER
        and find_period(quarters, quarters[-1].period_end, YEAR_AGO) is not None
    )
    if recent_quarters:
        ctx = _Context(quarters, years, False, as_of, settings)
        basis = "quarterly"
    elif years:
        ctx = _Context(years, years, True, as_of, settings)
        basis = "annual"
    else:
        ctx = _Context([], [], False, as_of, settings)
        basis = "none"

    latest = ctx.latest(4)
    eps_now = latest[0].eps if latest else None
    if ctx.annual_basis or len(latest) < 4 or any(p.eps is None for p in latest):
        trailing_positive = eps_now is not None and eps_now > 0
    else:
        trailing_positive = sum(p.eps or 0 for p in latest) > 0
    eps_path = eps_now is not None and eps_now > 0 and trailing_positive

    s = settings
    if eps_path:
        w = s.grade_weights
        components = [
            _scaled_growth(
                ctx,
                "eps_growth",
                "EPS growth",
                w.eps_growth,
                "eps",
                "EPS",
                s.eps_growth_q_min,
                s.eps_growth_q_strong,
            ),
            _acceleration(
                ctx, "eps_acceleration", "EPS acceleration", w.eps_acceleration, "eps", "EPS"
            ),
            _sales_growth_eps_path(ctx, w.sales_growth),
            _annual_eps_growth(ctx, w.annual_eps_growth),
            _roe(ctx, w.roe),
            _margins(ctx, w.margins, "Margin expansion"),
            _accumulation(s, up_down_volume, w.accumulation),
        ]
        bonus = w.insider_bonus
    else:
        r = s.revenue_grade_weights
        components = [
            _scaled_growth(
                ctx,
                "sales_growth",
                "Sales growth",
                r.sales_growth,
                "revenue",
                "sales",
                s.sales_growth_q_min,
                s.sales_growth_q_strong,
            ),
            _acceleration(
                ctx,
                "sales_acceleration",
                "Sales acceleration",
                r.sales_acceleration,
                "revenue",
                "sales",
            ),
            _margins(ctx, r.margins, "Margin trend"),
            _accumulation(s, up_down_volume, r.accumulation),
        ]
        bonus = s.grade_weights.insider_bonus
    components.append(_insider_bonus(s, insider_trades, as_of, bonus))

    scored = [c for c in components if not c.bonus]
    total = sum(c.max_points for c in scored)
    with_data = [c for c in scored if c.status != "no_data"]
    available = sum(c.max_points for c in with_data)
    coverage = 100 * available / total if total else 0.0
    if available <= 0 or coverage < s.grade_min_coverage_pct:
        return GradeResult(
            None, None, "eps" if eps_path else "revenue", basis, round(coverage, 1), components
        )
    earned = sum(c.points for c in with_data) / available * 100
    score = min(100.0, earned + sum(c.points for c in components if c.bonus))
    return GradeResult(
        _letter(score, s),
        round(score, 2),
        "eps" if eps_path else "revenue",
        basis,
        round(coverage, 1),
        components,
    )
