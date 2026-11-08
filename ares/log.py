"""Logging setup for the `ares` package: one configuration point, key=value lines.

Modules log with `logging.getLogger(__name__)` and *event=value* style messages, for example
``scenario_end scenario=3 kind=EVENT status=APPROVED round=2``. Nothing logs HTTP headers or prompt
bodies. LLM failures are logged by provider name, status and a short provider error message; that message
and every log line pass through `redact_secrets`, which masks configured key values and key-shaped tokens.

Level: ``ARES_LOG_LEVEL`` (DEBUG, INFO, WARNING, ERROR; default INFO).
"""
from __future__ import annotations

import logging
import os
import re

FORMAT = "%(asctime)s level=%(levelname)s logger=%(name)s %(message)s"
_HANDLER_NAME = "ares-default"
REDACTED = "[REDACTED]"

# Key-shaped tokens: OpenAI/OpenRouter (sk-...), Groq (gsk_...), Google (AIza...), bearer tokens, key=value pairs.
_SECRET_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_\-]{12,}"),
    re.compile(r"\bgsk_[A-Za-z0-9]{16,}"),
    re.compile(r"\bAIza[0-9A-Za-z_\-]{30,}"),
)
_BEARER = re.compile(r"(?i)\b(bearer\s+)[A-Za-z0-9._\-]{8,}")
_KEY_VALUE = re.compile(r"""(?i)\b((?:api[_-]?key|token|secret|password)["']?\s*[:=]\s*["']?)[^\s"',}]{8,}""")
_SECRET_ENV_SUFFIXES = ("_API_KEY", "_TOKEN", "_SECRET")


def _configured_secrets() -> list[str]:
    """Values of secret-looking environment variables (e.g. GROQ_API_KEY), longest first."""
    values = {v for k, v in os.environ.items() if k.upper().endswith(_SECRET_ENV_SUFFIXES) and len(v) >= 8}
    return sorted(values, key=len, reverse=True)


def redact_secrets(text: str) -> str:
    """Mask API keys and tokens in free text (provider error bodies, exception messages, log lines)."""
    if not text:
        return text
    for value in _configured_secrets():
        text = text.replace(value, REDACTED)
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub(REDACTED, text)
    text = _BEARER.sub(lambda m: m.group(1) + REDACTED, text)
    return _KEY_VALUE.sub(lambda m: m.group(1) + REDACTED, text)


class RedactingFilter(logging.Filter):
    """Handler filter: render the message once, redact secrets, freeze the result on the record."""

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        redacted = redact_secrets(message)
        if redacted != message:
            record.msg, record.args = redacted, None
        # tracebacks and stack info are appended by the formatter after the message: redact them too
        if record.exc_info and not record.exc_text:
            record.exc_text = logging.Formatter().formatException(record.exc_info)
        if record.exc_text:
            record.exc_text = redact_secrets(record.exc_text)
        if record.stack_info:
            record.stack_info = redact_secrets(record.stack_info)
        return True


def configure_logging(level: str | None = None) -> logging.Logger:
    """Attach one stderr handler to the `ares` logger (idempotent; safe under Streamlit reruns)."""
    logger = logging.getLogger("ares")
    name = (level or os.getenv("ARES_LOG_LEVEL") or "INFO").strip().upper()
    resolved = logging.getLevelName(name)
    logger.setLevel(resolved if isinstance(resolved, int) else logging.INFO)
    if not any(h.get_name() == _HANDLER_NAME for h in logger.handlers):
        handler = logging.StreamHandler()
        handler.set_name(_HANDLER_NAME)
        handler.setFormatter(logging.Formatter(FORMAT))
        handler.addFilter(RedactingFilter())  # applies to records from every ares.* child logger
        logger.addHandler(handler)
        # propagate stays True: Streamlit does not configure the root logger, and pytest's caplog relies on it
    if not isinstance(resolved, int):
        logger.warning("config: ARES_LOG_LEVEL=%r is not a logging level; using INFO", name)
    return logger
