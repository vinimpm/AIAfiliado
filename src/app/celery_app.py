import ssl as _ssl

from celery import Celery
from celery.schedules import crontab
from celery.signals import worker_ready

from app.config import settings

celery = Celery("aiafiliado")

_broker_conf: dict = {}
if settings.REDIS_TLS:
    _broker_conf = {
        "broker_use_ssl": {"ssl_cert_reqs": _ssl.CERT_NONE},
        "redis_backend_use_ssl": {"ssl_cert_reqs": _ssl.CERT_NONE},
    }

celery.conf.update(
    broker_url=settings.REDIS_URL,
    result_backend=settings.REDIS_URL,
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="America/Sao_Paulo",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_default_retry_delay=60,
    task_max_retries=3,
    task_soft_time_limit=1800,
    task_time_limit=3600,
    **_broker_conf,
)


@worker_ready.connect
def _start_healthcheck(**kwargs):
    from app.healthcheck import start_health_server

    start_health_server()


@celery.task(name="app.celery_app.emit_celery_queue_size")
def emit_celery_queue_size():
    """Emit current Celery queue size to CloudWatch every 5 minutes."""
    import redis as _redis

    from app.cloudwatch import emit_metric

    try:
        r = _redis.from_url(settings.REDIS_URL, socket_connect_timeout=5)
        size = r.llen("celery")
        emit_metric("CeleryQueueSize", size)
    except Exception:
        pass


celery.conf.beat_schedule = {
    "trigger-daily-pipeline": {
        "task": "services.orchestrator.trigger_daily_pipeline",
        "schedule": crontab(hour=7, minute=0),  # 07:00 BRT
    },
    "collect-all-metrics": {
        "task": "services.analytics_service.collect_all_metrics",
        "schedule": crontab(hour="*/6", minute=30),  # every 6h
    },
    "emit-celery-queue-size": {
        "task": "app.celery_app.emit_celery_queue_size",
        "schedule": 300,  # every 5 min
    },
}

celery.autodiscover_tasks(
    [
        "services.account_health",
        "services.trend_engine",
        "services.product_service",
        "services.script_service",
        "services.compliance",
        "services.video_service",
        "services.publish_service",
        "services.analytics_service",
        "services.orchestrator",
    ]
)
