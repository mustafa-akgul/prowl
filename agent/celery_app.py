"""Celery application — distributed task queue configuration.

Configures the Celery application for background review processing.
Uses Redis as the message broker and result backend.
"""

from __future__ import annotations

import logging
import os

from celery import Celery

logger = logging.getLogger(__name__)

# Local DB URL for testing windows since redis server isn't running
import platform

if platform.system() == "Windows":
    _URL_BROKER = "sqla+sqlite:///.data/celery_broker.sqlite"
    _URL_BACKEND = "db+sqlite:///.data/celery_backend.sqlite"
else:
    _URL_BROKER = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    _URL_BACKEND = _URL_BROKER

celery_app = Celery(
    "review_agent",
    broker=_URL_BROKER,
    backend=_URL_BACKEND,
)

celery_app.conf.update(
    # Task settings
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    # Worker settings
    worker_concurrency=4,
    worker_prefetch_multiplier=1,
    worker_max_tasks_per_child=100,
    # Task execution limits
    task_soft_time_limit=300,  # 5 minute soft limit
    task_time_limit=600,  # 10 minute hard limit
    # Result expiry
    result_expires=3600,  # Results expire after 1 hour
    # Retry settings
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    task_routes={
        "agent.worker.sub_agent_task": {"queue": "reviews"},
        "agent.worker.lead_agent_task": {"queue": "reviews"},
        "agent.worker.run_review_task": {"queue": "reviews"},
        "agent.worker.process_webhook_event": {"queue": "webhooks"},
    },
    task_default_queue="default",
)
