import base64
import os
import sys
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
