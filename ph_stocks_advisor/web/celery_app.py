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
from celery.signals import setup_logging

from ph_stocks_advisor.infra.config import get_settings
from ph_stocks_advisor.infra.logging import (
    LOG_FORMAT_CELERY,
    LOG_FORMAT_CELERY_TASK,
    configure_logging,
)

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
    worker_log_format=LOG_FORMAT_CELERY,
    worker_task_log_format=LOG_FORMAT_CELERY_TASK,
)


@setup_logging.connect
def _configure_celery_logging(loglevel: int | None = None, **_kwargs: object) -> None:
    """Take full control of logging setup so Celery does not install
    its own root handler on top of ours.

    Returning anything from a ``setup_logging`` handler tells Celery to
    skip its default ``logging.config.dictConfig`` call, which is what
    used to attach a *second* handler to ``ph_stocks_advisor.*`` and
    duplicate every line in the worker output.
    """
    configure_logging(loglevel)
    # Make sure our app logger inherits the worker's loglevel without
    # acquiring its own handler (propagation does the actual emitting).
    logging.getLogger("ph_stocks_advisor").setLevel(loglevel or logging.INFO)


# Auto-discover task modules inside the web package
celery_app.autodiscover_tasks(["ph_stocks_advisor.web"])
