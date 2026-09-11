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


@pytest.fixture(autouse=True)
def _block_real_github_pushes(monkeypatch):
    """Same class of incident as _block_real_telegram_sends above, for
    GitHub instead of Telegram: real incident (2026-09-11) -- this
    machine's shell environment has GITHUB_TOKEN/GITHUB_REPO set to a
    DIFFERENT project's repo (the sibling options-agent app), and
    github_state_sync.get_github_config() reads those raw env vars with
    no test-isolation awareness at all. trade_journal.save_journal (via
    push_journal_xlsx_to_github) isn't mocked by any individual test's
    own @patch the way send_message is -- so a plain local `pytest
    tests/` run was silently pushing fake seeded test data (trade IDs
    like "seed-1") straight into that OTHER project's real
    trade_journal.xlsx, dozens of times per run, discovered only
    because it also made the affected tests dramatically slower (real
    network round-trips instead of instant local test I/O).

    Fixed at the actual root -- the environment variables themselves --
    rather than patching every individual push function at every call
    site (state_paths.py, dashboard_state.py, trade_journal.py,
    github_state_sync.py all have one), since get_github_config()
    already treats missing credentials as a documented, safe no-op
    (returns None, every caller already degrades gracefully -- this is
    exactly the same "local dev without GITHUB_TOKEN" path every one of
    those modules' own docstrings already describes)."""
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_REPO", raising=False)
    monkeypatch.delenv("GITHUB_BRANCH", raising=False)
