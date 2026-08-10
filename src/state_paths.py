"""Central map of state files: repo-relative path -> local path. Both
dashboard_state.py and scan_results.py already default their local paths
to this same STATE_DIR, so this only needs to declare which of those
files are worth persisting through GitHub."""
import os

STATE_DIR = os.environ.get("STATE_DIR", os.path.join(os.path.dirname(__file__), "..", "config"))

STATE_FILES = {
    "config/dashboard_state.json": os.path.join(STATE_DIR, "dashboard_state.json"),
    "config/scan_results.json": os.path.join(STATE_DIR, "scan_results.json"),
    "config/trade_journal.json": os.path.join(STATE_DIR, "trade_journal.json"),
}
