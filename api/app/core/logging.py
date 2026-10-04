"""Structured logging. Everything (our code, uvicorn, arq, APScheduler) goes through structlog so
that production logs are one JSON object per line."""

import logging
import sys

import structlog

THIRD_PARTY_LOGGERS = ("uvicorn", "uvicorn.error", "uvicorn.access", "arq", "apscheduler")


class HealthNoiseFilter(logging.Filter):
    """Drop routine liveness chatter (successful health probes and heartbeat jobs, every few
    seconds per service) so real events stay readable. Failures are never dropped."""

    def filter(self, record: logging.LogRecord) -> bool:
        if record.name == "uvicorn.access" and isinstance(record.args, tuple):
            args = record.args
            if len(args) == 5 and str(args[2]).startswith("/api/health"):
                return not (isinstance(args[4], int) and args[4] < 400)
            return True
        if record.name.startswith(("arq.", "apscheduler.")):
            message = record.getMessage()
            if "cron:heartbeat" in message or "recording health" in message:
                return False
            if '"heartbeat (trigger' in message and "executed successfully" in message:
                return False
            return not message.startswith('Running job "heartbeat (trigger')
        return True


def configure_logging(level: str = "INFO", fmt: str = "json") -> None:
    shared: list[structlog.typing.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
    ]
    renderer: structlog.typing.Processor
    if fmt == "console":
        renderer = structlog.dev.ConsoleRenderer()
    else:
        shared.append(structlog.processors.format_exc_info)
        renderer = structlog.processors.JSONRenderer()

    structlog.configure(
        processors=[*shared, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            foreign_pre_chain=shared,
            processors=[structlog.stdlib.ProcessorFormatter.remove_processors_meta, renderer],
        )
    )
    handler.addFilter(HealthNoiseFilter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level.upper())

    for name in THIRD_PARTY_LOGGERS:
        lib_logger = logging.getLogger(name)
        lib_logger.handlers = []
        lib_logger.propagate = True


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    logger: structlog.stdlib.BoundLogger = structlog.get_logger(name)
    return logger
