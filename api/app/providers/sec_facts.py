"""SEC company facts (XBRL) → point-in-time quarterly and annual figures.

A company-facts document lists every value a company has filed, each with the period it covers
(start/end), the filing it came from (`accn`, `form`, `filed`) and that filing's fiscal labels
(`fy`, `fp`). The same period appears in many filings: first as the current period, later as a
comparative, sometimes restated. We keep every distinct value with its filing date, so a
calculation for date D sees exactly what was public at D.

Periods are recognised by length: 80 to 100 days is a quarter, 350 to 380 days a year (52/53-week
years included). 10-Qs also report year-to-date totals (6 and 9 months); a quarter that is never
filed on its own (almost always Q4) is the difference between consecutive year-to-date totals
that start on the same day, e.g. Q4 = full year - nine months. Those versions are `derived`.

Companies name the same figure differently and switch names over time (revenue moved to
`RevenueFromContractWithCustomer...` in 2018), so each figure has a priority list of concepts:
at each filing date a period takes its value from the first concept that has one.
"""

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from itertools import pairwise
from typing import Any

from app.providers.base import FinancialPeriod, PeriodKind

# Labels outside these are filer typos (one 10-Q gives fiscal year 43646, an Excel date
# serial): kept as unknown rather than stored. The periods themselves come from the dates.
FISCAL_YEARS = range(1900, 2101)
FISCAL_PERIOD_MAX_LEN = 4  # "FY", "Q1"... (fundamentals_*.fiscal_period is varchar(4))

QUARTER_DAYS = range(80, 101)
HALF_YEAR_DAYS = range(170, 196)
NINE_MONTH_DAYS = range(260, 291)
ANNUAL_DAYS = range(350, 381)
# A comparative period one fiscal year earlier ends 52 or 53 weeks (or one calendar year) before.
YEAR_AGO_DAYS = range(357, 372)

Concepts = tuple[tuple[str, str], ...]

DURATION_CONCEPTS: dict[str, Concepts] = {
    "eps_diluted": (
        ("us-gaap", "EarningsPerShareDiluted"),
        ("us-gaap", "EarningsPerShareBasicAndDiluted"),
        ("ifrs-full", "DilutedEarningsLossPerShare"),
        ("ifrs-full", "BasicAndDilutedEarningsLossPerShare"),
    ),
    "eps_basic": (
        ("us-gaap", "EarningsPerShareBasic"),
        ("us-gaap", "EarningsPerShareBasicAndDiluted"),
        ("ifrs-full", "BasicEarningsLossPerShare"),
        ("ifrs-full", "BasicAndDilutedEarningsLossPerShare"),
    ),
    "revenue": (
        ("us-gaap", "Revenues"),
        ("us-gaap", "RevenueFromContractWithCustomerExcludingAssessedTax"),
        ("us-gaap", "RevenueFromContractWithCustomerIncludingAssessedTax"),
        ("us-gaap", "SalesRevenueNet"),
        ("us-gaap", "RevenuesNetOfInterestExpense"),
        ("us-gaap", "SalesRevenueGoodsNet"),
        ("ifrs-full", "Revenue"),
        ("ifrs-full", "RevenueFromContractsWithCustomers"),
    ),
    "net_income": (
        ("us-gaap", "NetIncomeLoss"),
        ("us-gaap", "NetIncomeLossAvailableToCommonStockholdersBasic"),
        ("us-gaap", "ProfitLoss"),
        ("ifrs-full", "ProfitLossAttributableToOwnersOfParent"),
        ("ifrs-full", "ProfitLoss"),
    ),
    "operating_income": (
        ("us-gaap", "OperatingIncomeLoss"),
        ("ifrs-full", "ProfitLossFromOperatingActivities"),
    ),
}
INSTANT_CONCEPTS: dict[str, Concepts] = {
    "equity": (
        ("us-gaap", "StockholdersEquity"),
        ("us-gaap", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"),
        ("ifrs-full", "EquityAttributableToOwnersOfParent"),
        ("ifrs-full", "Equity"),
    ),
}
PER_SHARE = frozenset({"eps_diluted", "eps_basic"})
METRICS = (*DURATION_CONCEPTS, *INSTANT_CONCEPTS)


@dataclass(frozen=True, slots=True)
class Version:
    """A value as filed. Sorted by (filed, accession) it forms a point-in-time series."""

    filed: date
    value: float
    accession: str
    form: str | None
    derived: bool = False


Series = dict[date, list[Version]]  # period end → versions, oldest first


@dataclass(frozen=True, slots=True)
class _Fact:
    start: date | None
    end: date
    value: float
    filed: date
    accession: str
    form: str | None
    fy: int | None
    fp: str | None


def _as_of(versions: Sequence[Version], day: date) -> Version | None:
    """The latest version filed on or before `day`."""
    found = None
    for v in versions:
        if v.filed > day:
            break
        found = v
    return found


def _sorted(versions: Iterable[Version]) -> list[Version]:
    return sorted(versions, key=lambda v: (v.filed, v.accession))


def _units(facts: Mapping[str, Any], taxonomy: str, concept: str) -> Mapping[str, Any]:
    units: Mapping[str, Any] = facts.get(taxonomy, {}).get(concept, {}).get("units", {})
    return units


def _parse_facts(entries: Iterable[Mapping[str, Any]]) -> list[_Fact]:
    """Valid entries, one per (filing, period). Duplicates within a filing are dropped."""
    out: list[_Fact] = []
    seen: set[tuple[str, str | None, str]] = set()
    for e in entries:
        value = e.get("val")
        if not isinstance(value, int | float) or isinstance(value, bool):
            continue
        try:
            key = (str(e["accn"]), e.get("start"), str(e["end"]))
            filed = date.fromisoformat(e["filed"])
            end = date.fromisoformat(e["end"])
            start = date.fromisoformat(e["start"]) if e.get("start") else None
        except (KeyError, TypeError, ValueError):
            continue
        if key in seen:
            continue
        seen.add(key)
        fy, fp = e.get("fy"), e.get("fp")
        out.append(
            _Fact(
                start=start,
                end=end,
                value=float(value),
                filed=filed,
                accession=key[0],
                form=e.get("form"),
                fy=fy if isinstance(fy, int) and fy in FISCAL_YEARS else None,
                fp=fp if isinstance(fp, str) and 0 < len(fp) <= FISCAL_PERIOD_MAX_LEN else None,
            )
        )
    return out


def pick_currency(facts: Mapping[str, Any]) -> str | None:
    """The reporting currency: the money unit of revenue or net income with the most recent
    filing (a company that switched currencies is read in its current one)."""
    best: tuple[str, str] | None = None
    for metric in ("revenue", "net_income"):
        for taxonomy, concept in DURATION_CONCEPTS[metric]:
            for unit, entries in _units(facts, taxonomy, concept).items():
                if len(unit) != 3 or not unit.isalpha():
                    continue
                latest = max((str(e.get("filed", "")) for e in entries), default="")
                if best is None or latest > best[0]:
                    best = (latest, unit.upper())
    return best[1] if best else None


def _concept_facts(facts: Mapping[str, Any], taxonomy: str, concept: str, unit: str) -> list[_Fact]:
    for key, entries in _units(facts, taxonomy, concept).items():
        if key.upper() == unit.upper():
            return _parse_facts(entries)
    return []


def _difference(total: Sequence[Version], part: Sequence[Version]) -> list[Version]:
    """`total - part` at every date either changes, once both are known."""
    out: list[Version] = []
    for day in sorted({v.filed for v in total} | {v.filed for v in part}):
        t, p = _as_of(total, day), _as_of(part, day)
        if t is None or p is None:
            continue
        value = round(t.value - p.value, 6)
        if out and out[-1].value == value:
            continue
        latest = t if (t.filed, t.accession) >= (p.filed, p.accession) else p
        out.append(Version(day, value, latest.accession, latest.form, derived=True))
    return out


def first_known(candidates: Sequence[Sequence[Version]]) -> list[Version]:
    """Combine series in priority order: at every filing date, take the value of the first
    series that has one by then, and record a version whenever that value changes."""
    out: list[Version] = []
    for day in sorted({v.filed for versions in candidates for v in versions}):
        chosen = next(
            (v for versions in candidates if (v := _as_of(versions, day)) is not None), None
        )
        if chosen is None or (out and out[-1].value == chosen.value):
            continue
        out.append(Version(day, chosen.value, chosen.accession, chosen.form, chosen.derived))
    return out


def duration_series(found: list[_Fact]) -> tuple[Series, Series, dict[date, date]]:
    """Quarterly and annual series for one concept, plus each quarter's start date."""
    quarters: dict[date, list[Version]] = defaultdict(list)
    annuals: dict[date, list[Version]] = defaultdict(list)
    starts: dict[date, date] = {}
    cumulative: dict[date, dict[date, list[Version]]] = defaultdict(lambda: defaultdict(list))
    for f in found:
        if f.start is None:
            continue
        days = (f.end - f.start).days
        version = Version(f.filed, f.value, f.accession, f.form)
        if days in QUARTER_DAYS:
            quarters[f.end].append(version)
            starts.setdefault(f.end, f.start)
            cumulative[f.start][f.end].append(version)
        elif days in HALF_YEAR_DAYS or days in NINE_MONTH_DAYS:
            cumulative[f.start][f.end].append(version)
        elif days in ANNUAL_DAYS:
            annuals[f.end].append(version)
            cumulative[f.start][f.end].append(version)

    derived: dict[date, list[Version]] = {}
    for by_end in cumulative.values():
        ends = sorted(by_end)
        for previous, end in pairwise(ends):
            if (end - previous).days not in QUARTER_DAYS:
                continue
            derived[end] = _difference(_sorted(by_end[end]), _sorted(by_end[previous]))
            starts.setdefault(end, previous + timedelta(days=1))

    quarter_series: Series = {}
    for end in set(quarters) | set(derived):
        # A quarter filed on its own wins; the derived value only fills the gap until then.
        merged = first_known([_sorted(quarters.get(end, [])), derived.get(end, [])])
        if merged:
            quarter_series[end] = merged
    return quarter_series, {end: _sorted(v) for end, v in annuals.items()}, starts


def instant_series(found: list[_Fact]) -> Series:
    series: dict[date, list[Version]] = defaultdict(list)
    for f in found:
        if f.start is None:
            series[f.end].append(Version(f.filed, f.value, f.accession, f.form))
    return {end: _sorted(v) for end, v in series.items()}


def merge_by_priority(per_concept: Sequence[Series]) -> Series:
    """One series per period: at each filing date, the value of the highest-priority concept
    known by then. A period first filed under an older concept keeps its early versions."""
    ends = {e for series in per_concept for e in series}
    return {end: first_known([series.get(end, []) for series in per_concept]) for end in ends}


def _fiscal_labels(all_facts: Sequence[_Fact]) -> dict[tuple[PeriodKind, date], tuple[int, str]]:
    """Label each period with the fiscal year/period of the filing that first reported it as
    its current period (the latest period end in that filing). Q4 is the quarter that ends
    with a 10-K's fiscal year."""
    current_end: dict[str, date] = {}
    for f in all_facts:
        if f.start is not None and f.end > current_end.get(f.accession, date.min):
            current_end[f.accession] = f.end
    labels: dict[tuple[PeriodKind, date], tuple[int, str]] = {}
    for f in sorted(all_facts, key=lambda f: (f.filed, f.accession)):
        if f.start is None or f.fy is None or current_end[f.accession] != f.end:
            continue
        if f.fp == "FY":
            labels.setdefault((PeriodKind.ANNUAL, f.end), (f.fy, "FY"))
            labels.setdefault((PeriodKind.QUARTER, f.end), (f.fy, "Q4"))
        elif f.fp in ("Q1", "Q2", "Q3"):
            labels.setdefault((PeriodKind.QUARTER, f.end), (f.fy, f.fp))
    return labels


def _label_comparatives(
    labels: dict[tuple[PeriodKind, date], tuple[int, str]],
    periods: Iterable[tuple[PeriodKind, date]],
) -> None:
    """A period only ever seen as a comparative takes the label of the period one fiscal year
    later, with the year reduced by one (repeated, so older years chain back)."""
    pending = sorted((p for p in periods if p not in labels), key=lambda p: p[1], reverse=True)
    for kind, end in pending:
        for (other_kind, other_end), (fy, fp) in list(labels.items()):
            if other_kind == kind and (other_end - end).days in YEAR_AGO_DAYS:
                labels[(kind, end)] = (fy - 1, fp)
                break


def _assemble(
    kind: PeriodKind,
    metrics: Mapping[str, Series],
    starts: Mapping[date, date],
    labels: Mapping[tuple[PeriodKind, date], tuple[int, str]],
    currency: str | None,
) -> list[FinancialPeriod]:
    """Snapshots per period: a new version whenever any figure changes."""
    duration_ends = {end for name, s in metrics.items() if name in DURATION_CONCEPTS for end in s}
    out: list[FinancialPeriod] = []
    for end in sorted(duration_ends):
        start = starts.get(end)
        if start is None:
            continue
        per_metric = {name: s.get(end, []) for name, s in metrics.items()}
        previous: tuple[float | None, ...] | None = None
        days = sorted({v.filed for versions in per_metric.values() for v in versions})
        for day in days:
            known = {name: _as_of(versions, day) for name, versions in per_metric.items()}
            if not any(known[name] for name in DURATION_CONCEPTS):
                continue  # only an instant (equity) is known so far: not a period yet
            values = tuple(v.value if v else None for v in known.values())
            if values == previous:
                continue
            previous = values
            latest = max(
                (v for v in known.values() if v is not None), key=lambda v: (v.filed, v.accession)
            )
            fy, fp = labels.get((kind, end), (None, None))
            out.append(
                FinancialPeriod(
                    kind=kind,
                    period_start=start,
                    period_end=end,
                    reported_date=day,
                    fiscal_year=fy,
                    fiscal_period=fp,
                    form=latest.form,
                    accession=latest.accession,
                    currency=currency,
                    derived=any(v.derived for v in known.values() if v is not None),
                    **{name: (v.value if v else None) for name, v in known.items()},
                )
            )
    return out


def parse_financials(payload: Mapping[str, Any]) -> list[FinancialPeriod]:
    facts: Mapping[str, Any] = payload.get("facts", {})
    currency = pick_currency(facts)
    if currency is None:
        return []

    quarter_metrics: dict[str, Series] = {}
    annual_metrics: dict[str, Series] = {}
    quarter_starts: dict[date, date] = {}
    annual_starts: dict[date, date] = {}
    every_fact: list[_Fact] = []
    for name, concepts in DURATION_CONCEPTS.items():
        unit = f"{currency}/shares" if name in PER_SHARE else currency
        per_concept_q: list[Series] = []
        per_concept_a: list[Series] = []
        for taxonomy, concept in concepts:
            found = _concept_facts(facts, taxonomy, concept, unit)
            every_fact.extend(found)
            quarters, annuals, starts = duration_series(found)
            per_concept_q.append(quarters)
            per_concept_a.append(annuals)
            for end, start in starts.items():
                quarter_starts.setdefault(end, start)
            for f in found:
                if f.start is not None and (f.end - f.start).days in ANNUAL_DAYS:
                    annual_starts.setdefault(f.end, f.start)
        quarter_metrics[name] = merge_by_priority(per_concept_q)
        annual_metrics[name] = merge_by_priority(per_concept_a)
    for name, concepts in INSTANT_CONCEPTS.items():
        per_concept = [
            instant_series(_concept_facts(facts, taxonomy, concept, currency))
            for taxonomy, concept in concepts
        ]
        quarter_metrics[name] = annual_metrics[name] = merge_by_priority(per_concept)

    labels = _fiscal_labels(every_fact)
    _label_comparatives(
        labels,
        [(PeriodKind.QUARTER, e) for e in quarter_starts]
        + [(PeriodKind.ANNUAL, e) for e in annual_starts],
    )
    return _assemble(
        PeriodKind.QUARTER, quarter_metrics, quarter_starts, labels, currency
    ) + _assemble(PeriodKind.ANNUAL, annual_metrics, annual_starts, labels, currency)
