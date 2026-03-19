"""Centralized high-performance logging setup."""

from __future__ import annotations

import atexit
import json
import logging
import queue
import sys
import threading
from datetime import datetime
from logging.handlers import QueueHandler, QueueListener, RotatingFileHandler
from pathlib import Path

from config import settings

_LOCK = threading.Lock()
_LISTENER: QueueListener | None = None
_QUEUE_HANDLER: logging.Handler | None = None
_DOWNSTREAM_HANDLERS: list[logging.Handler] = []
_SPECIAL_HANDLERS: list[logging.Handler] = []
_CONFIGURED = False
_ATEXIT_REGISTERED = False


def _parse_level(value: str | int, default: int) -> int:
    if isinstance(value, int):
        return value
    text = str(value).strip()
    if not text:
        return default
    if text.isdigit():
        return int(text)
    level = getattr(logging, text.upper(), None)
    return level if isinstance(level, int) else default


class _IsoFormatter(logging.Formatter):
    def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:
        return datetime.fromtimestamp(record.created).astimezone().isoformat(timespec="milliseconds")


class _JsonFormatter(_IsoFormatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "module": record.module,
            "line": record.lineno,
            "process": record.process,
            "thread": record.threadName,
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        if record.stack_info:
            payload["stack"] = self.formatStack(record.stack_info)
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


class _DropAwareQueueHandler(QueueHandler):
    def __init__(self, q: queue.Queue[logging.LogRecord], drop_notice_every: int) -> None:
        super().__init__(q)
        self._drop_notice_every = max(1, int(drop_notice_every))
        self._drop_count = 0
        self._drop_lock = threading.Lock()

    def enqueue(self, record: logging.LogRecord) -> None:
        try:
            self.queue.put_nowait(record)
        except queue.Full:
            with self._drop_lock:
                self._drop_count += 1
                dropped = self._drop_count
            if dropped % self._drop_notice_every == 0:
                now = datetime.now().astimezone().isoformat(timespec="seconds")
                try:
                    sys.stderr.write(
                        f"{now} [logging] WARNING: log queue full, dropped={dropped}\n"
                    )
                    sys.stderr.flush()
                except Exception:
                    pass


def _build_formatter() -> logging.Formatter:
    if str(settings.log_format).strip().lower() == "json":
        return _JsonFormatter()
    return _IsoFormatter("%(asctime)s [%(name)s] %(levelname)s: %(message)s")


def _resolve_log_dir() -> Path:
    log_dir = Path(settings.log_dir or "logs")
    if not log_dir.is_absolute():
        log_dir = Path.cwd() / log_dir
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir


def _build_file_handler(path: Path, level: int, formatter: logging.Formatter) -> logging.Handler:
    max_bytes = max(1024, int(settings.log_file_max_bytes))
    backups = max(1, int(settings.log_file_backup_count))
    handler = RotatingFileHandler(
        filename=path,
        maxBytes=max_bytes,
        backupCount=backups,
        encoding="utf-8",
        delay=True,
    )
    handler.setLevel(level)
    handler.setFormatter(formatter)
    return handler


def _build_downstream_handlers(service_name: str, root_level: int) -> list[logging.Handler]:
    formatter = _build_formatter()
    handlers: list[logging.Handler] = []

    stdout_handler = logging.StreamHandler(stream=sys.stdout)
    stdout_handler.setLevel(root_level)
    stdout_handler.setFormatter(formatter)
    handlers.append(stdout_handler)

    if settings.log_file_enabled:
        log_dir = _resolve_log_dir()
        handlers.append(_build_file_handler(log_dir / f"{service_name}.log", root_level, formatter))
        if settings.log_error_file_enabled:
            handlers.append(
                _build_file_handler(
                    log_dir / f"{service_name}.error.log",
                    logging.ERROR,
                    formatter,
                )
            )

    return handlers


def _configure_feature_loggers() -> None:
    if not settings.log_file_enabled:
        return

    formatter = _build_formatter()
    log_dir = _resolve_log_dir()

    if settings.log_demon_roulette_enabled:
        demon_logger = logging.getLogger("demon_roulette")
        demon_logger.setLevel(_parse_level(settings.log_demon_roulette_level, logging.INFO))
        demon_logger.propagate = False
        for handler in demon_logger.handlers[:]:
            demon_logger.removeHandler(handler)
            try:
                handler.close()
            except Exception:
                pass
        demon_handler = _build_file_handler(
            log_dir / "demon_roulette.log",
            _parse_level(settings.log_demon_roulette_level, logging.INFO),
            formatter,
        )
        demon_logger.addHandler(demon_handler)
        _SPECIAL_HANDLERS.append(demon_handler)


def _set_library_levels() -> None:
    logging.getLogger("httpx").setLevel(_parse_level(settings.log_httpx_level, logging.WARNING))
    logging.getLogger("httpcore").setLevel(_parse_level(settings.log_httpcore_level, logging.WARNING))
    logging.getLogger("aiogram").setLevel(_parse_level(settings.log_aiogram_level, logging.INFO))


def setup_logging(service_name: str) -> None:
    """Setup root logging for a process."""
    global _ATEXIT_REGISTERED
    global _CONFIGURED
    global _DOWNSTREAM_HANDLERS
    global _LISTENER
    global _QUEUE_HANDLER
    global _SPECIAL_HANDLERS

    with _LOCK:
        if _CONFIGURED:
            return

        root = logging.getLogger()
        for handler in root.handlers[:]:
            root.removeHandler(handler)
            try:
                handler.close()
            except Exception:
                pass

        root_level = _parse_level(settings.log_level, logging.INFO)
        root.setLevel(root_level)
        logging.captureWarnings(True)
        logging.raiseExceptions = False

        downstream_handlers = _build_downstream_handlers(service_name, root_level)
        _DOWNSTREAM_HANDLERS = downstream_handlers

        if settings.log_queue_enabled:
            q_size = max(1000, int(settings.log_queue_size))
            q: queue.Queue[logging.LogRecord] = queue.Queue(maxsize=q_size)
            queue_handler = _DropAwareQueueHandler(
                q,
                drop_notice_every=max(1, int(settings.log_queue_drop_notice_every)),
            )
            queue_handler.setLevel(root_level)
            root.addHandler(queue_handler)
            listener = QueueListener(q, *downstream_handlers, respect_handler_level=True)
            listener.start()
            _QUEUE_HANDLER = queue_handler
            _LISTENER = listener
        else:
            for handler in downstream_handlers:
                root.addHandler(handler)

        _set_library_levels()
        _configure_feature_loggers()
        _CONFIGURED = True

        if not _ATEXIT_REGISTERED:
            atexit.register(shutdown_logging)
            _ATEXIT_REGISTERED = True


def shutdown_logging() -> None:
    """Flush and close logging resources."""
    global _CONFIGURED
    global _DOWNSTREAM_HANDLERS
    global _LISTENER
    global _QUEUE_HANDLER
    global _SPECIAL_HANDLERS

    with _LOCK:
        if _LISTENER is not None:
            try:
                _LISTENER.stop()
            except Exception:
                pass
            _LISTENER = None

        root = logging.getLogger()
        if _QUEUE_HANDLER is not None and _QUEUE_HANDLER in root.handlers:
            root.removeHandler(_QUEUE_HANDLER)
        _QUEUE_HANDLER = None

        for handler in _DOWNSTREAM_HANDLERS:
            try:
                handler.flush()
                handler.close()
            except Exception:
                pass
        _DOWNSTREAM_HANDLERS = []
        for handler in _SPECIAL_HANDLERS:
            try:
                handler.flush()
                handler.close()
            except Exception:
                pass
        _SPECIAL_HANDLERS = []
        _CONFIGURED = False
