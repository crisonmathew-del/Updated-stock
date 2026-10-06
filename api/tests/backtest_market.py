"""A small synthetic market for the backtest tests, drawn so the rules have something to do:

- SPOT: a VCP (tests.test_patterns) that breaks out on 2× volume at session 333 and runs up;
- A: the same VCP at half the price that breaks out on lighter volume (1.3M shares, ~151% of
  its 50-day average: confirmed at a 140% threshold, not at 160%), then collapses through its
  stop;
- GOOG: a cup with handle that breaks out at session 386;
- O: the VCP at 13% of the price: it dips under the $10 minimum in its first contraction (out
  of the scan), then comes back;
- AAPL, PDD (an ADR): steady advances (trend leaders without a base: watch setups);
- the benchmarks: steady advances.

SPOT also has quarterly results (one reported mid-walk, which lifts its Fundamentals Grade),
results releases for the earnings estimate, and a cluster of insider purchases.
"""

from collections.abc import Sequence
from datetime import date, timedelta

from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.redis import get_redis
from app.data.backfill import run_backfill
from app.data.universe import plan_universe, sync_tickers
from app.models import EarningsEvent, FundamentalsQuarterly, InsiderTransaction, Ticker
from tests.fakes import FakePrices, make_history
from tests.test_detection_pipeline import DAYS, START, drawn
from tests.test_patterns import CUP, VCP, VCP_VOLUME
from tests.test_universe import listed

LAST = 400
BREAKOUT = [*VCP, (333, 93.6), (345, 99.0), (360, 104.0), (375, 97.0), (LAST, 108.0)]
BREAKOUT_VOLUME = [*VCP_VOLUME, (333, 2.0), (334, 1.0)]
LIGHT_BREAKOUT_VOLUME = [*VCP_VOLUME, (333, 1.3), (334, 1.0)]
FAILED = [*VCP, (333, 93.6), (334, 92.0), (338, 80.0), (LAST, 78.0)]
CUP_BREAKOUT = [*CUP, (385, 92.5), (386, 99.0), (395, 103.0), (LAST, 105.0)]
CUP_VOLUME = [(0, 1.0), (386, 2.5), (387, 1.2)]
SYMBOLS = ("SPOT", "A", "GOOG", "O", "AAPL", "PDD")


def scaled(waypoints: Sequence[tuple[int, float]], factor: float) -> list[tuple[int, float]]:
    return [(i, p * factor) for i, p in waypoints]


async def seed_market(db: AsyncSession) -> dict[str, int]:
    """Tickers, bars through DAYS[LAST], and SPOT's fundamentals. Returns {symbol: id}."""
    plan = plan_universe(listed(*SYMBOLS))
    await sync_tickers(db, plan, START)
    last = DAYS[LAST]
    histories = {
        s: make_history(s, START, last, first_close=400, step=0.2) for s in plan if s not in SYMBOLS
    }
    histories["AAPL"] = make_history("AAPL", START, last, first_close=150, step=0.15)
    histories["PDD"] = make_history("PDD", START, last, first_close=60, step=0.08)
    histories["SPOT"] = drawn("SPOT", BREAKOUT, BREAKOUT_VOLUME)
    histories["A"] = drawn("A", scaled(FAILED, 0.5), LIGHT_BREAKOUT_VOLUME)
    histories["GOOG"] = drawn("GOOG", CUP_BREAKOUT, CUP_VOLUME)
    histories["O"] = drawn("O", scaled(BREAKOUT, 0.13), [(0, 30.0), (333, 60.0), (334, 30.0)])
    await run_backfill(db, FakePrices(histories), get_redis(), today=last, end=last, years=3)
    ids = {t.symbol: t.id for t in (await db.scalars(select(Ticker))).all()}
    await _fundamentals(db, ids["SPOT"])
    return ids


async def _fundamentals(db: AsyncSession, spot: int) -> None:
    def quarter(end: date, eps: float, revenue: float) -> dict[str, object]:
        return {
            "ticker_id": spot,
            "period_end": end,
            "reported_date": end + timedelta(days=30),
            "period_start": end - timedelta(days=90),
            "eps_diluted": eps,
            "revenue": revenue,
            "source": "test",
        }

    # Quarter ends every 91 days; the last one is reported at session ~350.
    reported_last = DAYS[350]
    ends = [reported_last - timedelta(days=30 + 91 * k) for k in range(8)][::-1]
    eps = [0.30, 0.32, 0.35, 0.36, 0.40, 0.46, 0.55, 0.72]
    revenue = [100e6, 104e6, 109e6, 112e6, 120e6, 132e6, 150e6, 175e6]
    await db.execute(
        insert(FundamentalsQuarterly),
        [quarter(e, x, r) for e, x, r in zip(ends, eps, revenue, strict=True)],
    )
    await db.execute(
        insert(EarningsEvent),
        [
            {
                "ticker_id": spot,
                "report_date": e + timedelta(days=30),
                "status": "reported",
                "timing": "after_close",
                "accession": None,
                "source": "test",
            }
            for e in ends
        ],
    )
    buys = [(DAYS[320], "0001"), (DAYS[324], "0002"), (DAYS[329], "0003")]
    await db.execute(
        insert(InsiderTransaction),
        [
            {
                "accession": f"000000000{n}-24-000001",
                "seq": 1,
                "ticker_id": spot,
                "filed_date": day + timedelta(days=2),
                "transaction_date": day,
                "insider_cik": cik,
                "insider_name": f"Insider {n}",
                "role": "Director",
                "is_director": True,
                "is_officer": False,
                "is_ten_percent_owner": False,
                "code": "P",
                "shares": 10_000,
                "price": 90.0,
                "source": "test",
            }
            for n, (day, cik) in enumerate(buys)
        ],
    )
    await db.commit()
