import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import state_paths as sp


def test_atomic_write_json_round_trips(tmp_path):
    path = str(tmp_path / "data.json")
    sp.atomic_write_json(path, {"a": 1, "b": [1, 2, 3]})
    assert sp.load_json_resilient(path, None) == {"a": 1, "b": [1, 2, 3]}


def test_atomic_write_json_creates_parent_directory(tmp_path):
    path = str(tmp_path / "nested" / "dir" / "data.json")
    sp.atomic_write_json(path, {"ok": True})
    assert os.path.exists(path)


def test_atomic_write_json_leaves_no_temp_file_behind(tmp_path):
    path = str(tmp_path / "data.json")
    sp.atomic_write_json(path, {"a": 1})
    remaining = os.listdir(tmp_path)
    assert remaining == ["data.json"]  # no .tmp-* leftover


def test_atomic_write_json_overwrites_existing_file(tmp_path):
    path = str(tmp_path / "data.json")
    sp.atomic_write_json(path, {"version": 1})
    sp.atomic_write_json(path, {"version": 2})
    assert sp.load_json_resilient(path, None) == {"version": 2}


def test_load_json_resilient_returns_default_when_file_missing(tmp_path):
    path = str(tmp_path / "does_not_exist.json")
    assert sp.load_json_resilient(path, {"fallback": True}) == {"fallback": True}


def test_load_json_resilient_returns_default_on_corrupt_json(tmp_path):
    # Regression test: a process killed mid-write (a real, documented
    # Render behavior -- not just idle sleep) used to leave a truncated
    # file that then raised json.JSONDecodeError on every subsequent
    # load, breaking the dashboard, both scan routes, the monitor, and
    # both nightly jobs until the next GitHub pull happened to restore
    # a good copy. Must degrade gracefully instead.
    path = str(tmp_path / "corrupt.json")
    with open(path, "w") as f:
        f.write('{"trades": [{"instrument": "EUR_USD", "statu')  # truncated mid-write

    assert sp.load_json_resilient(path, []) == []


def test_load_json_resilient_returns_real_data_on_valid_file(tmp_path):
    path = str(tmp_path / "data.json")
    sp.atomic_write_json(path, [{"trade_id": "1"}, {"trade_id": "2"}])
    assert sp.load_json_resilient(path, []) == [{"trade_id": "1"}, {"trade_id": "2"}]
