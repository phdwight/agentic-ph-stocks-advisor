"""
Celery application instance.

Separated from the task definitions so both the Flask web app and the
Celery worker can import the same ``celery_app`` without circular
dependencies.

The broker and result backend default to ``redis://localhost:6379/0``
and can be overridden via the ``REDIS_URL`` environment variable.
"""

from __future__ import annotations

import logging

from celery import Celery
from celery.signals import after_setup_logger

from ph_stocks_advisor.infra.config import get_settings

_settings = get_settings()

celery_app = Celery(
    "ph_stocks_advisor",
    broker=_settings.redis_url,
    backend=_settings.redis_url,
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    result_expires=3600,  # results kept for 1 hour
    task_track_started=True,
    worker_hijack_root_logger=False,
)


@after_setup_logger.connect
def _setup_app_logger(logger: logging.Logger, loglevel: int, **kwargs):
    """Propagate the worker's log level to the application logger.

    Celery's ``--loglevel`` flag only configures the ``celery.*`` loggers.
    This signal handler ensures ``ph_stocks_advisor.*`` loggers (including
    Tavily, data clients, etc.) also emit at the configured level.
    """
    app_logger = logging.getLogger("ph_stocks_advisor")
    app_logger.setLevel(loglevel)
    # Attach the same handlers so messages appear in the worker output.
    for handler in logger.handlers:
        app_logger.addHandler(handler)


# Auto-discover task modules inside the web package
celery_app.autodiscover_tasks(["ph_stocks_advisor.web"])
