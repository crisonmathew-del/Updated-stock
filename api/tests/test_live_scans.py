"""The pre-market scan and the intraday sweep on hand-worked numbers.

NVDA: previous close 100.00, 50-day average volume 1,000,000.
"""

from datetime import datetime

from app.core.calendar import MARKET_TZ
from app.intraday.volume import STANDARD_CURVE
from app.providers.base import Snapshot
from app.scanner.live_scans import Candidate, premarket_hits, sweep_hits
from app.settings.schema import AppSettings

SETTINGS = AppSettings()
NVDA = Candidate("NVDA", 1, "NVIDIA", 100.0, 1_000_000.0, "A", "near_pivot", earnings=True)
QUIET = Candidate("QUIET", 2, "Quiet Co", 50.0, 1_000_000.0)
PRE = datetime(2026, 10, 2, 8, 45, tzinfo=MARKET_TZ)
ELEVEN = datetime(2026, 10, 2, 11, 0, tzinfo=MARKET_TZ)


def snap(symbol: str, last: float, *, volume: int = 0, premarket: int = 0) -> Snapshot:
    return Snapshot(symbol, PRE, last, None, volume=volume, premarket_volume=premarket)


def test_premarket_gaps_need_size_and_volume() -> None:
    universe = {"NVDA": NVDA, "QUIET": QUIET}
    # +5.0% on 40,000 shares (4% of average): a hit. QUIET: -6% on 20,000 (2%): too thin.
    hits = premarket_hits(
        universe,
        {
            "NVDA": snap("NVDA", 105.0, premarket=40_000),
            "QUIET": snap("QUIET", 47.0, premarket=20_000),
        },
        SETTINGS,
        PRE,
    )
    assert [h.symbol for h in hits] == ["NVDA"]
    hit = hits[0]
    assert hit.title() == "NVDA gapping up 5.0% pre-market on earnings"
    assert hit.body() == (
        "105.00 vs the 100.00 close (+5.0%); pre-market volume 40,000, 4% of the 50-day "
        "average. An earnings reaction."
    )
    # +3.0% is under the 4% gap.
    assert (
        premarket_hits(universe, {"NVDA": snap("NVDA", 103.0, premarket=90_000)}, SETTINGS, PRE)
        == []
    )


def test_partial_feed_volume_is_scaled_up() -> None:
    # 1,000 IEX shares at a 2.5% share = 40,000 market-wide.
    hits = premarket_hits(
        {"NVDA": NVDA}, {"NVDA": snap("NVDA", 95.0, premarket=1_000)}, SETTINGS, PRE, 0.025
    )
    assert [(h.symbol, round(h.volume), h.title()) for h in hits] == [
        ("NVDA", 40_000, "NVDA gapping down 5.0% pre-market on earnings")
    ]


def test_the_sweep_wants_heavy_projected_volume_and_a_real_move() -> None:
    # At 11:00 (90 minutes) the standard curve has 30.5% of the day traded: 915,000 shares
    # project to 3,000,000, 3.0× average; +4.0% on the day.
    snaps = {
        "NVDA": snap("NVDA", 104.0, volume=915_000),
        "QUIET": snap("QUIET", 52.5, volume=200_000),
    }
    hits = sweep_hits({"NVDA": NVDA, "QUIET": QUIET}, snaps, SETTINGS, STANDARD_CURVE, ELEVEN)
    assert [(h.symbol, round(h.volume_pct)) for h in hits] == [("NVDA", 300)]
    assert hits[0].title() == "NVDA up on heavy volume"
    assert hits[0].body() == (
        "104.00 vs the 100.00 close (+4.0%) on projected volume 3.0× the 50-day average."
    )
    # Heavy volume but only +2%: no hit.
    flat = {"NVDA": snap("NVDA", 102.0, volume=915_000)}
    assert sweep_hits({"NVDA": NVDA}, flat, SETTINGS, STANDARD_CURVE, ELEVEN) == []
    # Too early for a projection.
    early = datetime(2026, 10, 2, 9, 32, tzinfo=MARKET_TZ)
    assert sweep_hits({"NVDA": NVDA}, snaps, SETTINGS, STANDARD_CURVE, early) == []
