import uuid
from contextvars import ContextVar

import structlog

from app.config import settings

_correlation_id: ContextVar[str] = ContextVar("correlation_id", default="")
_run_id: ContextVar[int | None] = ContextVar("run_id", default=None)


def set_correlation_id(cid: str | None = None) -> str:
    cid = cid or uuid.uuid4().hex[:12]
    _correlation_id.set(cid)
    return cid


def set_run_id(rid: int) -> None:
    _run_id.set(rid)


def add_context(logger, method_name, event_dict):
    cid = _correlation_id.get()
    if cid:
        event_dict["correlation_id"] = cid
    rid = _run_id.get()
    if rid is not None:
        event_dict["run_id"] = rid
    event_dict["env"] = settings.ENV
    return event_dict


def setup_logging() -> None:
    processors = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        add_context,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    if settings.ENV == "development":
        processors.append(structlog.dev.ConsoleRenderer())
    else:
        processors.append(structlog.processors.JSONRenderer())

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(0),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(service: str = "aiafiliado", **kwargs):
    setup_logging()
    return structlog.get_logger(service=service, **kwargs)
