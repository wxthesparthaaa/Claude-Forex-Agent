"""
Uses GitHub's Contents API as a free, durable state store -- Render's
free tier has no persistent disk, so anything written locally is wiped
on every redeploy. Identical approach to the sibling options-agent
project's github_state_sync.py (same GITHUB_TOKEN/GITHUB_REPO env vars
already set up on this account), ported rather than reinvented.

A no-op everywhere GITHUB_TOKEN/GITHUB_REPO aren't set, so local dev/
tests are unaffected.
"""
import base64
import json
import os
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Optional

from state_paths import STATE_DIR, STATE_FILES

API_BASE = "https://api.github.com"

# The most recent push attempt's outcome, across every push_state_to_
# github/push_binary_file call in this process -- previously a failure
# here was only ever a print() to a log nobody was watching. Read by
# app.py's dashboard route (get_sync_status) to show a warning banner
# when the last sync failed, so a trade that saved locally but never
# made it to GitHub (and would be silently lost on the next redeploy,
# since Render's free tier has no persistent disk) is at least visible
# rather than discovered after the fact.
_sync_status = {"ok": True, "last_error": None, "last_attempt_at": None}


def get_sync_status() -> dict:
    return dict(_sync_status)


def get_github_config() -> Optional[dict]:
    token = os.environ.get("GITHUB_TOKEN")
    repo = os.environ.get("GITHUB_REPO")
    if not token or not repo:
        return None
    return {"token": token, "repo": repo, "branch": os.environ.get("GITHUB_BRANCH", "main")}


def _github_request(method: str, url: str, token: str, body: Optional[dict] = None):
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return 404, None
        raise


def pull_state_from_github() -> int:
    config = get_github_config()
    if config is None:
        return 0

    os.makedirs(STATE_DIR, exist_ok=True)
    pulled = 0
    for repo_path, local_path in STATE_FILES.items():
        url = f"{API_BASE}/repos/{config['repo']}/contents/{repo_path}?ref={config['branch']}"
        status, data = _github_request("GET", url, config["token"])
        if status == 404 or data is None:
            continue
        content = base64.b64decode(data["content"]).decode("utf-8")
        with open(local_path, "w", encoding="utf-8") as f:
            f.write(content)
        pulled += 1
    return pulled


def _push_with_retry(repo_path: str, content_bytes: bytes, config: dict, max_retries: int = 3) -> bool:
    """PUTs content_bytes to repo_path via GitHub's Contents API,
    retrying on a 409 Conflict by re-fetching the current sha and trying
    again. GitHub's Contents API uses optimistic concurrency (the PUT
    must include the sha it read the file at; a 409 means something else
    wrote to it in between our read and our write) -- confirmed live,
    two scheduled jobs racing to save dashboard_state.json around the
    same 5-minute tick. Without a retry, the loser's update was just
    silently dropped (logged as a warning, never actually applied)."""
    url = f"{API_BASE}/repos/{config['repo']}/contents/{repo_path}"
    for attempt in range(max_retries):
        get_status, existing = _github_request("GET", f"{url}?ref={config['branch']}", config["token"])
        sha = existing["sha"] if get_status == 200 and existing else None

        body = {
            "message": f"Update {repo_path}",
            "content": base64.b64encode(content_bytes).decode("ascii"),
            "branch": config["branch"],
        }
        if sha:
            body["sha"] = sha

        try:
            status, _ = _github_request("PUT", url, config["token"], body=body)
            return status in (200, 201)
        except urllib.error.HTTPError as e:
            if e.code == 409 and attempt < max_retries - 1:
                continue  # sha went stale between our GET and PUT -- re-fetch and retry
            raise
    return False


def _tracked_push(repo_path: str, content_bytes: bytes, config: dict) -> bool:
    """Wraps _push_with_retry, recording the outcome into _sync_status
    so a failure is visible to get_sync_status() (and the dashboard
    banner it drives) instead of only ever a print() nobody was
    watching. Re-raises on failure, same as _push_with_retry itself --
    callers already catch and log a warning at their own call site
    (trade_journal.save_journal, dashboard_state.save_state), and this
    doesn't change that contract, just adds tracking alongside it."""
    try:
        ok = _push_with_retry(repo_path, content_bytes, config)
        _sync_status.update(ok=ok, last_error=None if ok else "push returned false",
                             last_attempt_at=datetime.now(timezone.utc).isoformat())
        return ok
    except Exception as e:
        _sync_status.update(ok=False, last_error=str(e), last_attempt_at=datetime.now(timezone.utc).isoformat())
        raise


def push_state_to_github(local_path: str) -> bool:
    config = get_github_config()
    if config is None:
        return False

    repo_path = next((rp for rp, lp in STATE_FILES.items() if lp == local_path), None)
    if repo_path is None:
        raise ValueError(f"{local_path} is not a known state file -- see state_paths.STATE_FILES")
    if not os.path.exists(local_path):
        return False

    with open(local_path, "r", encoding="utf-8") as f:
        content = f.read()

    return _tracked_push(repo_path, content.encode("utf-8"), config)


def push_binary_file(local_bytes: bytes, repo_path: str) -> bool:
    """Like push_state_to_github, but for arbitrary binary content (an
    .xlsx, not JSON text) at any repo path -- not restricted to
    state_paths.STATE_FILES, since this isn't local-disk app state,
    it's a standalone deliverable meant to be opened directly on
    GitHub, independent of the app's own runtime/redeploys."""
    config = get_github_config()
    if config is None:
        return False

    return _tracked_push(repo_path, local_bytes, config)


def github_file_url(repo_path: str) -> Optional[str]:
    """The browsable GitHub URL for a file pushed via push_binary_file --
    works whether or not the app/Render is even running, which is the
    point: a link that survives redeploys because it isn't served by
    the app at all."""
    config = get_github_config()
    if config is None:
        return None
    return f"https://github.com/{config['repo']}/blob/{config['branch']}/{repo_path}"
