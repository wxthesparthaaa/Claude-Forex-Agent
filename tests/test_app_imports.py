"""
Real incident: a dev-log edit to app.py's DEVELOPER_NOTES list dropped
the "DEVELOPER_NOTES = [" assignment line itself while splicing in a
new entry, leaving a bare tuple-literal expression statement at module
level -- a genuine IndentationError, not caught by anything in the
existing suite because nothing here ever actually imported app.py (it's
the Flask entry point; every other test targets src/ modules directly).
That's exactly the gap this file closes: importing app.py is cheap and
would have failed loudly on this exact bug the moment it was introduced,
instead of only surfacing once Render tried to boot gunicorn against a
module that can't even be parsed.

No OANDA/GitHub credentials needed -- app.py doesn't construct a real
OandaClient or make a live network call at import time (those all
happen lazily inside route handlers and scheduled jobs), only
pull_state_from_github() runs at import time and is itself a documented
no-op when GITHUB_TOKEN/GITHUB_REPO aren't set.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "src"))


def test_app_module_imports_without_error():
    import app
    assert app.app is not None  # the Flask instance itself


def test_developer_notes_is_a_real_list_of_date_text_tuples():
    import app
    assert isinstance(app.DEVELOPER_NOTES, list)
    assert len(app.DEVELOPER_NOTES) > 0
    for entry in app.DEVELOPER_NOTES:
        assert isinstance(entry, tuple) and len(entry) == 2
        date, text = entry
        assert isinstance(date, str) and isinstance(text, str)


def test_developer_notes_is_capped_at_5_entries():
    # Matches DEVELOPER_NOTES' own header comment: "5 MOST RECENT
    # entries only" -- enforced by a [:5] slice at definition time, not
    # by manual list trimming, so this stays true no matter how long the
    # full source list grows.
    import app
    assert len(app.DEVELOPER_NOTES) <= 5
