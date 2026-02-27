"""
Celery application instance.

Separated from the task definitions so both the Flask web app and the
Celery worker can import the same ``celery_app`` without circular
dependencies.

The broker and result backend default to ``redis://localhost:6379/0``
and can be overridden via the ``REDIS_URL`` environment variable.
"""

from __future__ import annotations

from celery import Celery

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

# Auto-discover task modules inside the web package
celery_app.autodiscover_tasks(["ph_stocks_advisor.web"])
