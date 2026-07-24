from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, TextIO

STANDARD_LOG_RECORD_KEYS = {
    "args",
    "asctime",
    "created",
    "exc_info",
    "exc_text",
    "filename",
    "funcName",
    "levelname",
    "levelno",
    "lineno",
    "module",
    "msecs",
    "message",
    "msg",
    "name",
    "pathname",
    "process",
    "processName",
    "relativeCreated",
    "stack_info",
    "thread",
    "threadName",
}


class DailyJsonFileHandler(logging.Handler):
    """Write one JSON-lines log file per local calendar date."""

    def __init__(self, log_dir: str | Path, prefix: str) -> None:
        super().__init__()
        self.log_dir = Path(log_dir)
        self.prefix = prefix
        self.current_date: date | None = None
        self.stream: TextIO | None = None
        self.log_dir.mkdir(parents=True, exist_ok=True)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            today = datetime.now().date()
            if today != self.current_date:
                self._open_for_date(today)
            if self.stream is None:
                return
            self.stream.write(self.format(record) + "\n")
            self.flush()
        except Exception:
            self.handleError(record)

    def flush(self) -> None:
        if self.stream is not None:
            self.stream.flush()

    def close(self) -> None:
        try:
            if self.stream is not None:
                self.stream.close()
                self.stream = None
        finally:
            super().close()

    def _open_for_date(self, target_date: date) -> None:
        if self.stream is not None:
            self.stream.close()
        self.current_date = target_date
        path = self.log_dir / f"{self.prefix}-{target_date.isoformat()}.jsonl"
        self.stream = path.open("a", encoding="utf-8")


class AuditLogFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return record.name.startswith("audit")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key in (
            "agent",
            "skill_id",
            "request_id",
            "run_id",
            "correlation_id",
            "task_id",
            "attempt",
            "duration_ms",
            "event",
            "method",
            "path",
            "query_string",
            "status_code",
            "client_ip",
            "user_agent",
            "request_headers",
            "request_body",
            "request_size_bytes",
            "response_body",
            "response_size_bytes",
            "error",
        ):
            value = getattr(record, key, None)
            if value is not None:
                payload[key] = value
        for key, value in record.__dict__.items():
            if key not in STANDARD_LOG_RECORD_KEYS and key not in payload:
                try:
                    json.dumps(value)
                except TypeError:
                    payload[key] = str(value)
                else:
                    payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


class PrettyFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        if getattr(record, "event", None) == "http_request":
            status_code = getattr(record, "status_code", "?")
            duration_ms = getattr(record, "duration_ms", "?")
            method = getattr(record, "method", "?")
            path = getattr(record, "path", "?")
            query_string = getattr(record, "query_string", "")
            request_id = getattr(record, "request_id", None)
            correlation_id = getattr(record, "correlation_id", None)
            request_body = getattr(record, "request_body", None)
            response_body = getattr(record, "response_body", None)
            route = f"{path}?{query_string}" if query_string else path
            line = (
                f"{timestamp} {record.levelname:<7} {method} {route} -> "
                f"{status_code} in {duration_ms}ms"
            )
            if request_id:
                line += f" request_id={request_id}"
            if correlation_id:
                line += f" correlation_id={correlation_id}"
            details: list[str] = []
            if request_body is not None:
                details.append("  request  " + _format_preview(request_body))
            if response_body is not None:
                details.append("  response " + _format_preview(response_body))
            if details:
                line += "\n" + "\n".join(details)
            return line

        base = f"{timestamp} {record.levelname:<7} {record.name}: {record.getMessage()}"
        if record.exc_info:
            base += "\n" + self.formatException(record.exc_info)
        return base


def _format_preview(value: Any) -> str:
    if isinstance(value, dict | list):
        return json.dumps(value, ensure_ascii=False, indent=2)
    return str(value)


def configure_logging(level: str, log_dir: str = "logs") -> None:
    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(PrettyFormatter())

    app_file_handler = DailyJsonFileHandler(log_dir, "app")
    app_file_handler.setFormatter(JsonFormatter())

    audit_file_handler = DailyJsonFileHandler(log_dir, "audit")
    audit_file_handler.setFormatter(JsonFormatter())
    audit_file_handler.addFilter(AuditLogFilter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(stream_handler)
    root.addHandler(app_file_handler)
    root.addHandler(audit_file_handler)
    root.setLevel(level.upper())
