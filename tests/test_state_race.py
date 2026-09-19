"""Regression tests for the 2026-09-16 state-overwrite incident: the nightly review's saved
strategy_realized_pnl / last_review_timestamp were reverted every night by another writer."""
import base64
import os
import sys
import threading
import time
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import dashboard_state as ds
import github_state_sync as gss


def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(ds, "STATE_DIR", str(tmp_path))
    monkeypatch.setattr(ds, "STATE_PATH", str(tmp_path / "dashboard_state.json"))
    monkeypatch.setattr(gss, "_synced_mtime", {})
    monkeypatch.setattr(gss, "_last_push_monotonic", {})


# ---------------------------------------------------------------- merge-on-save

def test_a_stale_writer_cannot_revert_another_writers_change(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    ds.save_state(ds.default_state())
    review = ds.load_state()
    heartbeat = ds.load_state()  # loaded BEFORE the review saves, saves AFTER -- the incident's shape

    review.strategy_realized_pnl = -943.18
    review.last_review_timestamp = "2026-09-18T17:03:33+00:00"
    ds.save_state(review)
    heartbeat.last_process_heartbeat_at = "2026-09-18T17:03:35+00:00"
    ds.save_state(heartbeat)

    final = ds.load_state()
    assert final.strategy_realized_pnl == -943.18
    assert final.last_review_timestamp == "2026-09-18T17:03:33+00:00"
    assert final.last_process_heartbeat_at == "2026-09-18T17:03:35+00:00"


def test_in_place_list_changes_are_detected_and_merged(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    ds.save_state(ds.default_state())
    a = ds.load_state()
    b = ds.load_state()

    a.strategy_realized_pnl = -5.0
    ds.save_state(a)
    b.risk_limit_skips_since_digest.append("VWAP Scalp: cooldown active")  # mutated in place, never reassigned
    ds.save_state(b)

    final = ds.load_state()
    assert final.strategy_realized_pnl == -5.0
    assert final.risk_limit_skips_since_digest == ["VWAP Scalp: cooldown active"]


def test_two_writers_changing_the_same_field_resolve_to_the_last_writer(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    ds.save_state(ds.default_state())
    a, b = ds.load_state(), ds.load_state()
    a.scan_digest_interval_minutes = 60
    b.scan_digest_interval_minutes = 240
    ds.save_state(a)
    ds.save_state(b)
    assert ds.load_state().scan_digest_interval_minutes == 240


def test_a_saved_object_picks_up_concurrent_changes_and_can_be_saved_again(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    ds.save_state(ds.default_state())
    a, b = ds.load_state(), ds.load_state()
    b.strategy_realized_pnl = -7.0
    ds.save_state(b)

    a.last_health_check_date = "2026-09-19"
    ds.save_state(a)
    assert a.strategy_realized_pnl == -7.0  # the caller's object now reflects what is on disk

    a.last_review_date = "2026-09-19"
    ds.save_state(a)
    final = ds.load_state()
    assert (final.strategy_realized_pnl, final.last_health_check_date, final.last_review_date) == \
        (-7.0, "2026-09-19", "2026-09-19")


def test_a_fresh_default_state_still_writes_in_full(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    state = ds.default_state()
    state.strategy_realized_pnl = -12.0
    ds.save_state(state)
    assert ds.load_state().strategy_realized_pnl == -12.0


# ---------------------------------------------------------------- pull must not clobber newer local state

def _pull_setup(tmp_path, monkeypatch, remote_text):
    monkeypatch.setenv("GITHUB_TOKEN", "tok")
    monkeypatch.setenv("GITHUB_REPO", "user/repo")
    local = str(tmp_path / "dashboard_state.json")
    monkeypatch.setattr(gss, "STATE_DIR", str(tmp_path))
    monkeypatch.setattr(gss, "STATE_FILES", {"config/dashboard_state.json": local})
    monkeypatch.setattr(gss, "_synced_mtime", {})
    monkeypatch.setattr(gss, "_last_push_monotonic", {})
    encoded = base64.b64encode(remote_text.encode()).decode()
    return local, patch("github_state_sync._github_request", return_value=(200, {"content": encoded, "sha": "s"}))


def test_pull_does_not_overwrite_local_changes_made_since_the_last_sync(tmp_path, monkeypatch):
    local, requests = _pull_setup(tmp_path, monkeypatch, '{"strategy_realized_pnl": -731.7}')  # stale GitHub copy
    with open(local, "w") as f:
        f.write('{"strategy_realized_pnl": -943.2}')
    gss._synced_mtime[local] = gss._mtime_ns(local) - 5  # last confirmed sync predates this local write

    with requests:
        pulled = gss.pull_state_from_github()

    assert pulled == 0
    assert '-943.2' in open(local).read()


def test_pull_does_not_overwrite_a_file_pushed_moments_ago(tmp_path, monkeypatch):
    local, requests = _pull_setup(tmp_path, monkeypatch, '{"strategy_realized_pnl": -731.7}')
    with open(local, "w") as f:
        f.write('{"strategy_realized_pnl": -943.2}')
    gss._synced_mtime[local] = gss._mtime_ns(local)
    gss._last_push_monotonic[local] = time.monotonic()  # GitHub may still serve the previous version

    with requests:
        assert gss.pull_state_from_github() == 0
    assert '-943.2' in open(local).read()


def test_pull_still_updates_a_clean_local_file(tmp_path, monkeypatch):
    local, requests = _pull_setup(tmp_path, monkeypatch, '{"mode": "demo", "from": "github"}')
    with open(local, "w") as f:
        f.write('{"mode": "old"}')
    gss._synced_mtime[local] = gss._mtime_ns(local)  # in sync, no recent push

    with requests:
        assert gss.pull_state_from_github() == 1
    assert '"from": "github"' in open(local).read()


# ---------------------------------------------------------------- pushes carry the newest content, in order

def test_pushes_are_serialized_so_an_older_read_cannot_land_last(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    monkeypatch.setenv("GITHUB_TOKEN", "tok")
    monkeypatch.setenv("GITHUB_REPO", "user/repo")
    local = str(tmp_path / "dashboard_state.json")
    monkeypatch.setattr(gss, "STATE_FILES", {"config/dashboard_state.json": local})
    with open(local, "w") as f:
        f.write("v1")

    landed = []
    calls = {"n": 0}

    def slow_push(repo_path, content_bytes, config):
        calls["n"] += 1
        if calls["n"] == 1:
            time.sleep(0.3)  # only the FIRST push is slow, like one unlucky GitHub round trip
        landed.append(content_bytes.decode())
        return True

    monkeypatch.setattr(gss, "_tracked_push", slow_push)

    first = threading.Thread(target=gss.push_state_to_github, args=(local,))
    first.start()
    time.sleep(0.05)
    with open(local, "w") as f:
        f.write("v2")  # the file is updated while the first push is still in flight
    second = threading.Thread(target=gss.push_state_to_github, args=(local,))
    second.start()
    first.join(2)
    second.join(2)

    assert landed[-1] == "v2"
