"""Central map of state files: repo-relative path -> local path. Both
dashboard_state.py and scan_results.py already default their local paths
to this same STATE_DIR, so this only needs to declare which of those
files are worth persisting through GitHub.

Also the shared atomic-write / resilient-load helpers all three state
modules (trade_journal, dashboard_state, scan_results) use -- factored
out here since it's already a common, dependency-free import for all
three, rather than duplicated three times."""
import json
import os
import tempfile

STATE_DIR = os.environ.get("STATE_DIR", os.path.join(os.path.dirname(__file__), "..", "config"))

STATE_FILES = {
    "config/dashboard_state.json": os.path.join(STATE_DIR, "dashboard_state.json"),
    "config/scan_results.json": os.path.join(STATE_DIR, "scan_results.json"),
    "config/trade_journal.json": os.path.join(STATE_DIR, "trade_journal.json"),
}


def atomic_write_json(path: str, data) -> None:
    """Writes `data` as JSON to `path` without ever leaving a
    truncated/corrupt file behind if the process is killed mid-write.
    Render has genuinely killed this process mid-run before (a real,
    documented crash/restart-loop, not just idle sleep) -- a plain
    open(path, "w") + json.dump can leave a half-written file exactly
    in that window, and every subsequent load_*() call then fails until
    the next successful GitHub pull restores a good copy. Writes to a
    temp file in the SAME directory first, then os.replace()s it into
    place -- os.replace is atomic on both POSIX and Windows as long as
    source and destination are on the same filesystem, which same-
    directory guarantees."""
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=directory, prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise


def load_json_resilient(path: str, default):
    """Loads JSON from `path`; returns `default` if the file doesn't
    exist OR fails to parse. A corrupted/truncated file (e.g. left over
    from a mid-write kill before atomic_write_json existed, or any
    other on-disk corruption) degrades to "as if nothing was ever
    saved" instead of raising and breaking every caller -- the
    dashboard, both scan routes, the monitor, both nightly jobs -- until
    the next successful save or GitHub pull happens to restore a good
    copy."""
    if not os.path.exists(path):
        return default
    try:
        with open(path) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        print(f"WARNING: {path} is corrupt or unreadable ({e}) -- treating as empty until the next "
              f"successful save or GitHub pull restores it", flush=True)
        return default
