"""Central error logging.

Goal: whenever the app hits an unhandled exception — a request that 500s, a
worker solve that crashes, or any uncaught error that takes the process down —
write a full traceback to a log file so it can be read after the fact. This is
especially useful on Windows where a crashing console window vanishes before you
can read it.

Log file: each process writes to its own timestamped file under ``logs/``, e.g.
``logs/shiftwork_2026-08-23_12-17-26.log``. A per-run name (rather than a single
overwritten ``log.txt``) means a new run never clobbers the evidence from the one
that failed. Override the exact path with the ``SHIFTWORK_LOG`` env var.

Design choices:
  - The file handler uses ``delay=True`` and ERROR level, and it creates the
    ``logs/`` directory only when it first writes — so a clean run leaves no file
    and no folder, and the log never fills with routine INFO chatter.
  - ``sys.excepthook`` is replaced so an otherwise-uncaught exception (e.g. a
    crash during startup, after imports have succeeded) is logged before the
    process dies — not just printed to a console that may disappear.
  - ``setup_logging`` is idempotent and adds NO console handler (uvicorn already
    logs to the console), so importing it never duplicates console output or
    interferes with tests.

Call ``setup_logging()`` once from each process entry point (the web app import
and the worker entry point).
"""

from __future__ import annotations

import logging
import os
import sys
from datetime import datetime

# Project root = the directory containing the `webapp` package.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_LOG_DIR = os.path.join(_REPO_ROOT, "logs")

_configured = False
_log_path: str | None = None


def _default_log_path() -> str:
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    return os.path.join(_LOG_DIR, f"shiftwork_{stamp}.log")


def log_path() -> str:
    """The resolved log file path for this process (or where it would be)."""
    if _log_path is not None:
        return _log_path
    return os.environ.get("SHIFTWORK_LOG") or _default_log_path()


class _DirCreatingFileHandler(logging.FileHandler):
    """A FileHandler that creates the parent directory on first write.

    Combined with delay=True this keeps clean runs free of both the log file and
    the logs/ directory — they appear only when there's actually an error.
    """

    def _open(self):
        os.makedirs(os.path.dirname(self.baseFilename), exist_ok=True)
        return super()._open()


def setup_logging(path: str | None = None) -> str:
    """Install the error file handler and the uncaught-exception hook.

    Idempotent: repeat calls are a no-op and return the same path. Returns the
    path errors will be written to.
    """
    global _configured, _log_path
    if _configured:
        return _log_path  # type: ignore[return-value]

    target = path or os.environ.get("SHIFTWORK_LOG") or _default_log_path()

    file_handler = _DirCreatingFileHandler(target, encoding="utf-8", delay=True)
    file_handler.setLevel(logging.ERROR)
    file_handler.setFormatter(
        logging.Formatter(
            "%(asctime)s %(levelname)s %(name)s [pid %(process)d]: %(message)s"
        )
    )

    root = logging.getLogger()
    if root.level == logging.NOTSET or root.level > logging.INFO:
        root.setLevel(logging.INFO)
    root.addHandler(file_handler)

    # Route uncaught exceptions to the log (then fall back to the default so the
    # traceback still prints to stderr). KeyboardInterrupt is left alone.
    def _excepthook(exc_type, exc_value, exc_tb):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_tb)
            return
        logging.getLogger("shiftwork").critical(
            "Uncaught exception", exc_info=(exc_type, exc_value, exc_tb)
        )
        sys.__excepthook__(exc_type, exc_value, exc_tb)

    sys.excepthook = _excepthook

    _configured = True
    _log_path = target
    return target
