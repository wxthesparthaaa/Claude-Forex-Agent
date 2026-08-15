"""
Global test-suite safety net: no test should be able to send a real
Telegram message, even if it forgets to mock send_message itself.

Real incident: a test in test_scheduled_jobs.py was missing that mock,
and every local `pytest tests/` run silently sent a genuine "Potential
trades tonight" message via whichever bot credentials the local
config/telegram_config.properties fallback happened to hold at the
time -- the actual cause of a full day of "phantom" duplicate-
notification reports that had nothing to do with the deployed app,
Render, or the scheduler at all.

Patches send_message at each module's own import site (not
telegram_notifier.send_message itself, which test_notifications.py
deliberately exercises for real -- with urllib.request.urlopen mocked
underneath instead -- to test its own behavior), so any other test that
reaches a Telegram-sending code path is protected automatically even
if that specific test forgets to mock it locally. Redundant with an
individual test's own @patch on the same target -- harmless, just an
extra layer.
"""
import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest


@pytest.fixture(autouse=True)
def _block_real_telegram_sends():
    with patch("scheduled_jobs.send_message"), \
         patch("trade_execution.send_message"), \
         patch("trade_monitor.send_message"):
        yield
