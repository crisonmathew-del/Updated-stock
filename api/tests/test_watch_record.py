"""The breakout watch's track record: hits, misses and picks still pending."""

from datetime import date

from app.core.calendar import sessions_between
from app.scanner.watch_record import track_record

SESSIONS = sessions_between(date(2026, 9, 1), date(2026, 10, 20))  # 35 sessions


def test_hits_misses_and_pending_picks() -> None:
    picks = [
        (date(2026, 9, 1), 1),  # breaks out 3 sessions later: a hit
        (date(2026, 9, 2), 2),  # breaks out 12 sessions later: too late, a miss
        (date(2026, 9, 3), 3),  # never breaks out: a miss
        (date(2026, 10, 14), 4),  # 4 sessions ago: still pending
        (date(2026, 10, 15), 5),  # broke out the next session: a hit, even though recent
    ]
    breakouts = [
        (date(2026, 8, 20), 1),  # before the pick: ignored
        (date(2026, 9, 4), 1),
        (date(2026, 9, 18), 2),
        (date(2026, 10, 16), 5),
    ]
    record = track_record(picks, breakouts, SESSIONS, within=10)
    assert record == {
        "picks": 5,
        "hits": 2,
        "misses": 2,
        "pending": 1,
        "within_sessions": 10,
        "hit_rate_pct": 50.0,
        "median_sessions_to_breakout": 2.0,  # 3 and 1 sessions
        "since": date(2026, 9, 1),
    }


def test_no_picks_yet() -> None:
    record = track_record([], [], [], within=10)
    assert (record["picks"], record["hit_rate_pct"], record["since"]) == (0, None, None)
