import logging

import pytest

from app.core.logging import HealthNoiseFilter


def _record(name: str, msg: str, args: tuple[object, ...] | None = None) -> logging.LogRecord:
    return logging.LogRecord(name, logging.INFO, __file__, 1, msg, args, None)


ACCESS_FORMAT = '%s - "%s %s HTTP/%s" %d'


@pytest.mark.parametrize(
    ("record", "kept"),
    [
        (
            _record("uvicorn.access", ACCESS_FORMAT, ("1.2.3.4", "GET", "/api/health", "1.1", 200)),
            False,
        ),
        (
            _record(
                "uvicorn.access", ACCESS_FORMAT, ("1.2.3.4", "GET", "/api/health/ready", "1.1", 200)
            ),
            False,
        ),
        (
            _record(
                "uvicorn.access", ACCESS_FORMAT, ("1.2.3.4", "GET", "/api/health/ready", "1.1", 503)
            ),
            True,
        ),
        (
            _record(
                "uvicorn.access", ACCESS_FORMAT, ("1.2.3.4", "GET", "/api/stocks/AAPL", "1.1", 200)
            ),
            True,
        ),
        (_record("arq.worker", "  1.00s → cron:heartbeat()"), False),
        (_record("arq.worker", "recording health: j_complete=1"), False),
        (_record("arq.worker", "  0.20s → eod_scan()"), True),
        (
            _record(
                "apscheduler.executors.default",
                'Running job "heartbeat (trigger: interval[0:00:10])"',
            ),
            False,
        ),
        (
            _record(
                "apscheduler.executors.default",
                'Job "heartbeat (trigger: interval[0:00:10])" executed successfully',
            ),
            False,
        ),
        (
            _record(
                "apscheduler.executors.default",
                'Job "heartbeat (trigger: interval[0:00:10])" raised an exception',
            ),
            True,
        ),
        (_record("apscheduler.executors.default", 'Running job "eod_scan (trigger: cron)"'), True),
        (_record("app.main", "api.startup"), True),
    ],
)
def test_only_successful_liveness_chatter_is_dropped(record: logging.LogRecord, kept: bool) -> None:
    assert HealthNoiseFilter().filter(record) is kept
