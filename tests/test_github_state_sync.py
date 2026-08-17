import base64
import os
import sys
import urllib.error
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import github_state_sync as gss


def test_get_github_config_none_when_unset(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_REPO", raising=False)
    assert gss.get_github_config() is None


def test_get_github_config_reads_env_vars(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "tok")
    monkeypatch.setenv("GITHUB_REPO", "user/repo")
    config = gss.get_github_config()
    assert config == {"token": "tok", "repo": "user/repo", "branch": "main"}


def test_pull_state_from_github_is_noop_without_config(monkeypatch, tmp_path):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_REPO", raising=False)
    assert gss.pull_state_from_github() == 0


@patch("github_state_sync._github_request")
def test_pull_state_from_github_writes_local_files(mock_request, monkeypatch, tmp_path):
    monkeypatch.setenv("GITHUB_TOKEN", "tok")
    monkeypatch.setenv("GITHUB_REPO", "user/repo")
    monkeypatch.setattr(gss, "STATE_DIR", str(tmp_path))
    fake_local_path = str(tmp_path / "dashboard_state.json")
    monkeypatch.setattr(gss, "STATE_FILES", {"config/dashboard_state.json": fake_local_path})

    content = '{"mode": "demo"}'
    encoded = base64.b64encode(content.encode()).decode()
    mock_request.return_value = (200, {"content": encoded, "sha": "abc123"})

    pulled = gss.pull_state_from_github()

    assert pulled == 1
    with open(fake_local_path) as f:
        assert f.read() == content


@patch("github_state_sync._github_request")
def test_pull_state_from_github_skips_missing_files(mock_request, monkeypatch, tmp_path):
    monkeypatch.setenv("GITHUB_TOKEN", "tok")
    monkeypatch.setenv("GITHUB_REPO", "user/repo")
    monkeypatch.setattr(gss, "STATE_DIR", str(tmp_path))
    monkeypatch.setattr(gss, "STATE_FILES", {"config/missing.json": str(tmp_path / "missing.json")})
    mock_request.return_value = (404, None)

    assert gss.pull_state_from_github() == 0


@patch("github_state_sync._github_request")
def test_pull_state_from_github_isolates_a_degraded_file_instead_of_crashing(mock_request, monkeypatch, tmp_path):
    # Real incident: this is called bare at app.py's module-import time,
    # before Flask/gunicorn ever binds to the port. A degraded GitHub API
    # (504 Gateway Timeout) let an unhandled HTTPError propagate straight
    # out of this loop, crashing the ENTIRE app on every boot attempt --
    # Render's own port-scanner then saw "no open HTTP ports" and kept
    # retrying the same crashing import into a boot-crash loop for as
    # long as GitHub stayed degraded. One file's failure must not abort
    # the whole pull, and pull_state_from_github itself must never raise.
    monkeypatch.setenv("GITHUB_TOKEN", "tok")
    monkeypatch.setenv("GITHUB_REPO", "user/repo")
    monkeypatch.setattr(gss, "STATE_DIR", str(tmp_path))
    degraded_path = str(tmp_path / "dashboard_state.json")
    monkeypatch.setattr(gss, "STATE_FILES", {"config/dashboard_state.json": degraded_path})

    mock_request.side_effect = urllib.error.HTTPError(url="u", code=504, msg="Gateway Timeout", hdrs=None, fp=None)

    pulled = gss.pull_state_from_github()  # must not raise

    assert pulled == 0
    assert not os.path.exists(degraded_path)  # left untouched, not partially written


@patch("github_state_sync._github_request")
def test_pull_state_from_github_still_pulls_other_files_when_one_fails(mock_request, monkeypatch, tmp_path):
    monkeypatch.setenv("GITHUB_TOKEN", "tok")
    monkeypatch.setenv("GITHUB_REPO", "user/repo")
    monkeypatch.setattr(gss, "STATE_DIR", str(tmp_path))
    degraded_path = str(tmp_path / "degraded.json")
    healthy_path = str(tmp_path / "healthy.json")
    monkeypatch.setattr(gss, "STATE_FILES", {
        "config/degraded.json": degraded_path,
        "config/healthy.json": healthy_path,
    })

    content = '{"mode": "demo"}'
    encoded = base64.b64encode(content.encode()).decode()
    degraded = urllib.error.HTTPError(url="u", code=504, msg="Gateway Timeout", hdrs=None, fp=None)
    mock_request.side_effect = [degraded, (200, {"content": encoded, "sha": "abc"})]

    pulled = gss.pull_state_from_github()

    assert pulled == 1
    with open(healthy_path) as f:
        assert f.read() == content


def test_push_state_to_github_false_without_config(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_REPO", raising=False)
    assert gss.push_state_to_github("/some/path.json") is False


def test_push_state_to_github_false_when_local_file_missing(monkeypatch, tmp_path):
    monkeypatch.setenv("GITHUB_TOKEN", "tok")
    monkeypatch.setenv("GITHUB_REPO", "user/repo")
    missing_path = str(tmp_path / "nope.json")
    monkeypatch.setattr(gss, "STATE_FILES", {"config/nope.json": missing_path})
    assert gss.push_state_to_github(missing_path) is False


def test_push_state_to_github_raises_for_unknown_file(monkeypatch, tmp_path):
    monkeypatch.setenv("GITHUB_TOKEN", "tok")
    monkeypatch.setenv("GITHUB_REPO", "user/repo")
    unknown_path = str(tmp_path / "unknown.json")
    open(unknown_path, "w").write("{}")
    try:
        gss.push_state_to_github(unknown_path)
        assert False, "expected ValueError"
    except ValueError:
        pass


@patch("github_state_sync._github_request")
def test_push_state_to_github_includes_sha_when_file_exists_remotely(mock_request, monkeypatch, tmp_path):
    monkeypatch.setenv("GITHUB_TOKEN", "tok")
    monkeypatch.setenv("GITHUB_REPO", "user/repo")
    local_path = str(tmp_path / "dashboard_state.json")
    open(local_path, "w").write('{"mode": "demo"}')
    monkeypatch.setattr(gss, "STATE_FILES", {"config/dashboard_state.json": local_path})

    mock_request.side_effect = [
        (200, {"sha": "existing-sha"}),  # the GET-before-PUT existence check
        (200, {}),                        # the PUT itself
    ]

    result = gss.push_state_to_github(local_path)

    assert result is True
    put_call = mock_request.call_args_list[1]
    assert put_call[0][0] == "PUT"
    assert put_call[1]["body"]["sha"] == "existing-sha"


@patch("github_state_sync._github_request")
def test_push_state_to_github_retries_on_409_conflict(mock_request, monkeypatch, tmp_path):
    # Real incident: two scheduled jobs raced to save dashboard_state.json
    # around the same 5-minute tick, and the loser's PUT got rejected
    # with "409 Conflict" (its sha went stale in between its own GET and
    # PUT) -- previously that just propagated as an unhandled error and
    # the update was silently lost. Must re-fetch the fresh sha and
    # succeed on retry instead of giving up.
    monkeypatch.setenv("GITHUB_TOKEN", "tok")
    monkeypatch.setenv("GITHUB_REPO", "user/repo")
    local_path = str(tmp_path / "dashboard_state.json")
    open(local_path, "w").write('{"mode": "demo"}')
    monkeypatch.setattr(gss, "STATE_FILES", {"config/dashboard_state.json": local_path})

    conflict = urllib.error.HTTPError(url="u", code=409, msg="Conflict", hdrs=None, fp=None)
    mock_request.side_effect = [
        (200, {"sha": "stale-sha"}),  # first GET
        conflict,                      # first PUT -- rejected, sha went stale
        (200, {"sha": "fresh-sha"}),  # retry GET -- picks up the current sha
        (200, {}),                     # retry PUT -- succeeds
    ]

    result = gss.push_state_to_github(local_path)

    assert result is True
    assert mock_request.call_count == 4
    final_put = mock_request.call_args_list[3]
    assert final_put[0][0] == "PUT"
    assert final_put[1]["body"]["sha"] == "fresh-sha"


@patch("github_state_sync._github_request")
def test_push_state_to_github_gives_up_after_max_retries(mock_request, monkeypatch, tmp_path):
    monkeypatch.setenv("GITHUB_TOKEN", "tok")
    monkeypatch.setenv("GITHUB_REPO", "user/repo")
    local_path = str(tmp_path / "dashboard_state.json")
    open(local_path, "w").write('{"mode": "demo"}')
    monkeypatch.setattr(gss, "STATE_FILES", {"config/dashboard_state.json": local_path})

    conflict = urllib.error.HTTPError(url="u", code=409, msg="Conflict", hdrs=None, fp=None)
    # every GET returns a sha, every PUT conflicts -- should give up after 3 attempts, not loop forever
    mock_request.side_effect = [
        (200, {"sha": "sha-1"}), conflict,
        (200, {"sha": "sha-2"}), conflict,
        (200, {"sha": "sha-3"}), conflict,
    ]

    try:
        gss.push_state_to_github(local_path)
        assert False, "expected the 409 to propagate after exhausting retries"
    except urllib.error.HTTPError as e:
        assert e.code == 409
    assert mock_request.call_count == 6


def test_push_binary_file_false_without_config(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_REPO", raising=False)
    assert gss.push_binary_file(b"data", "trade_journal.xlsx") is False


@patch("github_state_sync._github_request")
def test_push_binary_file_base64_encodes_raw_bytes(mock_request, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "tok")
    monkeypatch.setenv("GITHUB_REPO", "user/repo")
    mock_request.side_effect = [(404, None), (201, {})]  # not yet on GitHub, then created

    result = gss.push_binary_file(b"\x00\x01binarydata", "trade_journal.xlsx")

    assert result is True
    put_call = mock_request.call_args_list[1]
    assert put_call[0][0] == "PUT"
    assert base64.b64decode(put_call[1]["body"]["content"]) == b"\x00\x01binarydata"
    assert "sha" not in put_call[1]["body"]  # new file, no existing sha


def test_github_file_url_none_without_config(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_REPO", raising=False)
    assert gss.github_file_url("trade_journal.xlsx") is None


def test_github_file_url_builds_blob_link(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "tok")
    monkeypatch.setenv("GITHUB_REPO", "wxthesparthaaa/Claude-Forex-Agent")
    url = gss.github_file_url("trade_journal.xlsx")
    assert url == "https://github.com/wxthesparthaaa/Claude-Forex-Agent/blob/main/trade_journal.xlsx"


def _reset_sync_status():
    gss._sync_status.update(ok=True, last_error=None, last_attempt_at=None)


@patch("github_state_sync._github_request")
def test_get_sync_status_records_a_successful_push(mock_request, monkeypatch, tmp_path):
    # Regression test: a push failure used to be only a print() at each
    # caller's own try/except, with no way for the dashboard to know
    # about it. get_sync_status() is what a warning banner reads.
    _reset_sync_status()
    monkeypatch.setenv("GITHUB_TOKEN", "tok")
    monkeypatch.setenv("GITHUB_REPO", "user/repo")
    local_path = str(tmp_path / "dashboard_state.json")
    open(local_path, "w").write('{"mode": "demo"}')
    monkeypatch.setattr(gss, "STATE_FILES", {"config/dashboard_state.json": local_path})
    mock_request.side_effect = [(200, {"sha": "abc"}), (200, {})]

    gss.push_state_to_github(local_path)

    status = gss.get_sync_status()
    assert status["ok"] is True
    assert status["last_error"] is None
    assert status["last_attempt_at"] is not None


@patch("github_state_sync._github_request")
def test_get_sync_status_records_a_failed_push(mock_request, monkeypatch, tmp_path):
    _reset_sync_status()
    monkeypatch.setenv("GITHUB_TOKEN", "tok")
    monkeypatch.setenv("GITHUB_REPO", "user/repo")
    local_path = str(tmp_path / "dashboard_state.json")
    open(local_path, "w").write('{"mode": "demo"}')
    monkeypatch.setattr(gss, "STATE_FILES", {"config/dashboard_state.json": local_path})
    conflict = urllib.error.HTTPError(url="u", code=409, msg="Conflict", hdrs=None, fp=None)
    mock_request.side_effect = [(200, {"sha": "s1"}), conflict, (200, {"sha": "s2"}), conflict,
                                 (200, {"sha": "s3"}), conflict]

    try:
        gss.push_state_to_github(local_path)
    except urllib.error.HTTPError:
        pass

    status = gss.get_sync_status()
    assert status["ok"] is False
    assert status["last_error"] is not None
    assert status["last_attempt_at"] is not None


def test_get_sync_status_stays_ok_when_github_is_not_configured(monkeypatch, tmp_path):
    # Not configured (no GITHUB_TOKEN locally) must not read as a
    # failure -- only a genuine attempt that failed should ever flip
    # ok=False, or every local dev run would show a false-positive
    # warning banner.
    _reset_sync_status()
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_REPO", raising=False)
    local_path = str(tmp_path / "dashboard_state.json")
    open(local_path, "w").write("{}")

    gss.push_state_to_github(local_path)

    assert gss.get_sync_status()["ok"] is True
