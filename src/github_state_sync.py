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
from typing import Optional

from state_paths import STATE_DIR, STATE_FILES

API_BASE = "https://api.github.com"


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

    url = f"{API_BASE}/repos/{config['repo']}/contents/{repo_path}"
    get_status, existing = _github_request("GET", f"{url}?ref={config['branch']}", config["token"])
    sha = existing["sha"] if get_status == 200 and existing else None

    body = {
        "message": f"Update {repo_path}",
        "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
        "branch": config["branch"],
    }
    if sha:
        body["sha"] = sha

    status, _ = _github_request("PUT", url, config["token"], body=body)
    return status in (200, 201)
