"""``@log_call`` decorator for tracing internal service calls.

Captures function name, sanitized argument *names* (not values), duration_ms,
outcome (``ok``/``error``), and exception class on failure. Correlation context
(request_id, username, session_id, ...) is pulled automatically by structlog
from contextvars, so call sites do not need to thread it manually.
"""
from __future__ import annotations

import asyncio
import functools
import inspect
import time
from typing import Any, Callable

import structlog

_logger = structlog.get_logger("mobaide.call")


def log_call(
    name: str | None = None,
    level: str = "info",
    log_args: tuple[str, ...] = (),
):
    """Decorate a function to emit ``call.start`` / ``call.end`` log records.

    Args:
        log_args: Allowlist of parameter names whose values are safe to include
            in the log record. All other argument values are suppressed; only
            the parameter names appear.
    """

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        call_name = name or f"{func.__module__}.{func.__qualname__}"
        sig = inspect.signature(func)
        is_coro = asyncio.iscoroutinefunction(func)

        def _bind_safe_args(args: tuple, kwargs: dict) -> dict[str, Any]:
            try:
                bound = sig.bind_partial(*args, **kwargs)
                bound.apply_defaults()
            except TypeError:
                return {"args_count": len(args), "kwargs_keys": list(kwargs)}
            params = bound.arguments
            arg_names = [n for n in params if n != "self"]
            extra: dict[str, Any] = {"arg_names": arg_names}
            for k in log_args:
                if k in params:
                    extra[k] = params[k]
            return extra

        log_method = getattr(_logger, level, _logger.info)

        if is_coro:

            @functools.wraps(func)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                start = time.perf_counter()
                extra = _bind_safe_args(args, kwargs)
                log_method("call.start", call=call_name, **extra)
                try:
                    result = await func(*args, **kwargs)
                except Exception as exc:
                    duration_ms = (time.perf_counter() - start) * 1000
                    _logger.error(
                        "call.end",
                        call=call_name,
                        outcome="error",
                        duration_ms=round(duration_ms, 2),
                        error_type=type(exc).__name__,
                        error_message=str(exc),
                        **extra,
                    )
                    raise
                duration_ms = (time.perf_counter() - start) * 1000
                log_method(
                    "call.end",
                    call=call_name,
                    outcome="ok",
                    duration_ms=round(duration_ms, 2),
                    **extra,
                )
                return result

            return async_wrapper

        @functools.wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            start = time.perf_counter()
            extra = _bind_safe_args(args, kwargs)
            log_method("call.start", call=call_name, **extra)
            try:
                result = func(*args, **kwargs)
            except Exception as exc:
                duration_ms = (time.perf_counter() - start) * 1000
                _logger.error(
                    "call.end",
                    call=call_name,
                    outcome="error",
                    duration_ms=round(duration_ms, 2),
                    error_type=type(exc).__name__,
                    error_message=str(exc),
                    **extra,
                )
                raise
            duration_ms = (time.perf_counter() - start) * 1000
            log_method(
                "call.end",
                call=call_name,
                outcome="ok",
                duration_ms=round(duration_ms, 2),
                **extra,
            )
            return result

        return sync_wrapper

    return decorator
