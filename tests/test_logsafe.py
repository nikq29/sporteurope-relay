import logging
import sys

from relay.logsafe import RedactingFormatter, redact_url


def test_redact_url_strips_query():
    assert redact_url("https://stream.mux.com/abc.m3u8?token=SECRET") == "https://stream.mux.com/abc.m3u8"


def test_formatter_strips_queries_from_messages_and_tracebacks():
    try:
        raise RuntimeError("GET https://x.mux.com/r.m3u8?signature=SECRET2 failed")
    except RuntimeError:
        record = logging.LogRecord(
            "t", logging.ERROR, __file__, 1, "fetch %s", ("https://a.example/b?token=SECRET1",), sys.exc_info()
        )
    out = RedactingFormatter("%(message)s").format(record)
    assert "SECRET1" not in out and "SECRET2" not in out
    assert "https://a.example/b" in out and "https://x.mux.com/r.m3u8" in out
