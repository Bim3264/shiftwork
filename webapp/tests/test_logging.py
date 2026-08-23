"""Tests for the error-logging setup.

Verifies the behaviour the feature promises:
  - no log file is created on a clean run (lazy; ERROR-level only),
  - an actual error writes a full traceback to the file,
  - the uncaught-exception hook is installed.

The tests isolate global logging state: they detach the root logger's existing
handlers for the duration and restore them afterwards, so they neither pollute
the real log.txt nor leak handlers into other tests.

Run from the project root:  python3 -m unittest webapp.tests.test_logging
"""

import logging
import os
import sys
import tempfile
import unittest

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)

from webapp import logging_setup as ls  # noqa: E402


class LoggingSetupTest(unittest.TestCase):

    def setUp(self):
        # Detach and remember the root logger's handlers so our temp handler is
        # the only one active during the test (no writes to the real log.txt).
        self._root = logging.getLogger()
        self._saved_handlers = self._root.handlers[:]
        for h in self._saved_handlers:
            self._root.removeHandler(h)
        self._saved_level = self._root.level
        self._saved_excepthook = sys.excepthook
        self._saved_configured = ls._configured
        self._saved_path = ls._log_path
        ls._configured = False
        ls._log_path = None

        fd, self._path = tempfile.mkstemp(suffix=".log")
        os.close(fd)
        os.unlink(self._path)  # must not exist yet

    def tearDown(self):
        for h in self._root.handlers[:]:
            self._root.removeHandler(h)
            try:
                h.close()
            except Exception:
                pass
        for h in self._saved_handlers:
            self._root.addHandler(h)
        self._root.setLevel(self._saved_level)
        sys.excepthook = self._saved_excepthook
        ls._configured = self._saved_configured
        ls._log_path = self._saved_path
        if os.path.exists(self._path):
            os.unlink(self._path)

    def test_no_file_on_clean_run(self):
        ls.setup_logging(self._path)
        self.assertFalse(os.path.exists(self._path), "file created before any error")
        logging.getLogger("shiftwork.test").info("routine info")
        self.assertFalse(
            os.path.exists(self._path), "INFO should not create the error log"
        )

    def test_error_writes_traceback(self):
        ls.setup_logging(self._path)
        try:
            raise ValueError("kaboom-marker-123")
        except ValueError:
            logging.getLogger("shiftwork.test").exception("failure while testing")

        for h in self._root.handlers:
            try:
                h.flush()
            except Exception:
                pass

        self.assertTrue(os.path.exists(self._path), "error did not create the log")
        content = open(self._path, encoding="utf-8").read()
        self.assertIn("kaboom-marker-123", content)   # the exception message
        self.assertIn("Traceback", content)           # the actual traceback
        self.assertIn("failure while testing", content)

    def test_excepthook_is_installed(self):
        before = sys.excepthook
        ls.setup_logging(self._path)
        self.assertIsNot(sys.excepthook, before)
        self.assertIsNot(sys.excepthook, sys.__excepthook__)


if __name__ == "__main__":
    unittest.main(verbosity=2)
