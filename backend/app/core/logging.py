"""Structured JSON logging.

Every background job records start / finish / duration / provider / record
counts so the system-health dashboard has real numbers to show.
"""

from __future__ import annotations

import json
import logging
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Dict, Iterator


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: Dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        extra = getattr(record, "extra_fields", None)
        if extra:
            payload.update(extra)
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(level: int = logging.INFO) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)
    # yfinance / urllib3 are chatty
    for noisy in ("urllib3", "httpx", "httpcore", "apscheduler.scheduler",
                  "apscheduler.executors.default"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    logging.getLogger("yfinance").setLevel(logging.ERROR)
    logging.getLogger("peewee").setLevel(logging.ERROR)


def log_event(logger: logging.Logger, message: str, **fields: Any) -> None:
    logger.info(message, extra={"extra_fields": fields})


class JobRun(dict):
    """Mutable record a job fills in while it runs."""

    def __init__(self, job: str) -> None:
        super().__init__(
            job=job,
            provider=None,
            records_received=0,
            records_saved=0,
            records_rejected=0,
            errors=[],
        )

    def error(self, message: str) -> None:
        self["errors"].append(message)


@contextmanager
def job_logger(logger: logging.Logger, job: str) -> Iterator[JobRun]:
    """Context manager that logs job start/finish with a duration and counters."""
    run = JobRun(job)
    started = time.perf_counter()
    log_event(logger, "job.start", job=job)
    try:
        yield run
    except Exception as exc:  # noqa: BLE001 - jobs must never kill the scheduler
        run.error(f"{type(exc).__name__}: {exc}")
        log_event(
            logger,
            "job.failed",
            duration_ms=round((time.perf_counter() - started) * 1000, 2),
            **run,
        )
        raise
    else:
        log_event(
            logger,
            "job.finish",
            duration_ms=round((time.perf_counter() - started) * 1000, 2),
            **run,
        )
