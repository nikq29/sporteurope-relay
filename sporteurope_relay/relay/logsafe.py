"""Logging that never leaks tokens: every URL loses its query string."""
import logging
import re

_URL_WITH_QUERY = re.compile(r"(https?://[^\s?\"'<>]+)\?[^\s\"'<>]*")


def redact_url(url: str) -> str:
    return url.split("?", 1)[0]


def redact_text(text: str) -> str:
    return _URL_WITH_QUERY.sub(r"\1?…", text)


class RedactingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return redact_text(super().format(record))


def setup_logging(level: int = logging.INFO) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(RedactingFormatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
