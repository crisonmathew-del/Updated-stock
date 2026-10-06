"""Compute and store the Fundamentals Grade for every stock as of a session.

Reads every stored statement version (the grade picks what was reported by then), the 50-day
up/down volume ratio, recent insider purchases and splits, then writes one
`fundamental_grades` row per ticker for that date (replacing an earlier run for the date).
"""

from collections import defaultdict
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

import polars as pl
from sqlalchemy import delete, insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.loaders import read_frame
from app.fundamentals.grade import (
    GradeResult,
    InsiderTrade,
    Split,
    StatementRow,
    grade_fundamentals,
)
from app.models import FundamentalGrade
from app.settings.schema import AppSettings

CHUNK = 1000


Reader = Callable[[str], Awaitable[pl.DataFrame]]


def _ids(ticker_ids: Sequence[int]) -> str:
    return ",".join(str(int(t)) for t in ticker_ids)


async def _statements(
    table: str, ids: str, as_of: date, read: Reader
) -> dict[int, list[StatementRow]]:
    frame = await read(
        "SELECT ticker_id, period_end, reported_date, fiscal_year, fiscal_period, "
        "coalesce(eps_diluted, eps_basic) AS eps, revenue, net_income, operating_income, "
        f"equity, derived, currency FROM {table} "
        f"WHERE ticker_id IN ({ids}) AND reported_date <= '{as_of.isoformat()}'"
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


@dataclass
class GradeInputs:
    quarterly: list[StatementRow] = field(default_factory=list)
    annual: list[StatementRow] = field(default_factory=list)
    up_down_volume: float | None = None
    insiders: list[InsiderTrade] = field(default_factory=list)
    splits: list[Split] = field(default_factory=list)

    def grade(self, as_of: date, settings: AppSettings) -> GradeResult:
        return grade_fundamentals(
            self.quarterly,
            self.annual,
            as_of=as_of,
            settings=settings,
            up_down_volume=self.up_down_volume,
            insider_trades=self.insiders,
            splits=self.splits,
        )


async def load_grade_inputs(
    ticker_ids: Sequence[int], as_of: date, settings: AppSettings, read: Reader = read_frame
) -> dict[int, GradeInputs]:
    """Everything the grade needs for these stocks, as known on `as_of`. Request handlers pass
    `read=partial(query_frame, session)` (one stock: pooled connection beats connectorx)."""
    out: dict[int, GradeInputs] = defaultdict(GradeInputs)
    if not ticker_ids:
        return out
    ids = _ids(ticker_ids)
    for tid, rows in (await _statements("fundamentals_quarterly", ids, as_of, read)).items():
        out[tid].quarterly = rows
    for tid, rows in (await _statements("fundamentals_annual", ids, as_of, read)).items():
        out[tid].annual = rows
    volume = await read(
        "SELECT ticker_id, up_down_volume_50 FROM indicators_daily "
        f"WHERE ticker_id IN ({ids}) AND date = '{as_of.isoformat()}'"
    )
    for tid, ratio in volume.iter_rows():
        out[int(tid)].up_down_volume = ratio
    window = as_of - timedelta(days=settings.insider_cluster_window_days)
    trades = await read(
        "SELECT ticker_id, transaction_date, filed_date, insider_cik, insider_name, code, "
        "is_director, is_officer FROM insider_transactions "
        f"WHERE ticker_id IN ({ids}) AND code = 'P' AND filed_date <= '{as_of.isoformat()}' "
        f"AND transaction_date > '{window.isoformat()}'"
    )
    for r in trades.iter_rows(named=True):
        out[int(r["ticker_id"])].insiders.append(
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
    actions = await read(
        "SELECT ticker_id, ex_date, value FROM corporate_actions "
        f"WHERE ticker_id IN ({ids}) AND kind = 'split' AND ex_date <= '{as_of.isoformat()}'"
    )
    for tid, ex_date, ratio in actions.iter_rows():
        out[int(tid)].splits.append(Split(ex_date, float(ratio)))
    return out


async def grade_stocks(
    session: AsyncSession, settings: AppSettings, as_of: date, ticker_ids: Sequence[int]
) -> dict[str, int]:
    """Grade `ticker_ids` as of `as_of` and store the results. Returns {grade: count}, with
    "n/a" for stocks without enough data."""
    counts: dict[str, int] = defaultdict(int)
    for start in range(0, len(ticker_ids), CHUNK):
        chunk = list(ticker_ids[start : start + CHUNK])
        inputs = await load_grade_inputs(chunk, as_of, settings)
        rows: list[dict[str, Any]] = []
        for tid in chunk:
            result = inputs[tid].grade(as_of, settings)
            counts[result.grade or "n/a"] += 1
            rows.append(
                {
                    "ticker_id": tid,
                    "date": as_of,
                    "grade": result.grade,
                    "score": result.score,
                    "path": result.path,
                    "basis": result.basis,
                    "coverage_pct": result.coverage_pct,
                    "components": result.components_json(),
                }
            )
        await session.execute(
            delete(FundamentalGrade).where(
                FundamentalGrade.date == as_of, FundamentalGrade.ticker_id.in_(chunk)
            )
        )
        if rows:
            await session.execute(insert(FundamentalGrade), rows)
        await session.commit()
    return dict(sorted(counts.items()))
