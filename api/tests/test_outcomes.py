"""Signal outcomes worked by hand on a drawn path: signal close 100, entry 100.10, stop 93.90
(risk 6.20, so 2R = 112.50)."""

from app.scanner.outcomes import PathBar, measure
from tests.pattern_fixtures import sessions

DAYS = sessions(70)


def path(
    closes: list[float], highs: dict[int, float] | None = None, lows: dict[int, float] | None = None
) -> list[PathBar]:
    """Session k (1-based) after the signal closes at closes[k-1], ±1 unless overridden."""
    highs, lows = highs or {}, lows or {}
    return [
        PathBar(DAYS[k], highs.get(k, c + 1), lows.get(k, c - 1), c)
        for k, c in enumerate(closes, start=1)
    ]


def test_a_winner_over_the_full_horizon() -> None:
    # Up 0.5 a session for 60 sessions: closes 100.5, 101, ..., 130.
    closes = [100 + 0.5 * k for k in range(1, 61)]
    outcome = measure(100.0, path(closes), entry=100.10, stop=93.90)
    assert outcome.returns == {1: 0.5, 5: 2.5, 10: 5.0, 20: 10.0, 60: 30.0}
    assert (outcome.mfe_pct, outcome.mae_pct) == (31.0, -0.5)  # high 131; low 100.5 - 1
    # 2R = 100.10 + 2 × 6.20 = 112.50: first high ≥ 112.50 is session 23 (close 111.5 + 1).
    assert outcome.target_2r_on == DAYS[23]
    # +20% = 120: first high ≥ 120 is session 38 (close 119 + 1).
    assert outcome.gain_20_on == DAYS[38]
    assert (outcome.stop_hit_on, outcome.sessions_observed, outcome.complete) == (None, 60, True)


def test_a_loser_still_in_progress() -> None:
    closes = [99.0, 97.0, 95.0, 96.0]
    outcome = measure(100.0, path(closes, lows={3: 93.5}), entry=100.10, stop=93.90)
    assert outcome.returns == {1: -1.0, 5: None, 10: None, 20: None, 60: None}
    assert (outcome.mfe_pct, outcome.mae_pct) == (0.0, -6.5)  # high 99 + 1; low 93.5
    assert outcome.stop_hit_on == DAYS[3]
    assert (outcome.target_2r_on, outcome.gain_20_on) == (None, None)
    assert (outcome.sessions_observed, outcome.complete) == (4, False)


def test_without_a_plan_and_without_sessions() -> None:
    outcome = measure(50.0, path([60.0]))  # no entry/stop: no stop or 2R dates
    assert (outcome.returns[1], outcome.gain_20_on) == (20.0, DAYS[1])  # high 61 ≥ 60
    assert (outcome.stop_hit_on, outcome.target_2r_on) == (None, None)
    empty = measure(50.0, [])
    assert (empty.sessions_observed, empty.mfe_pct, empty.complete) == (0, None, False)


def test_only_the_first_60_sessions_count() -> None:
    closes = [100.0] * 60 + [200.0]
    outcome = measure(100.0, path(closes))
    assert (outcome.mfe_pct, outcome.sessions_observed) == (1.0, 60)
