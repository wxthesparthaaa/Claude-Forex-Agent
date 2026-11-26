"""Tests for the read-only headline archive (src/headline_archive.py). No network: every source and push is stubbed."""
import json
import os
from datetime import datetime, timedelta, timezone

import pytest

import headline_archive as ha

NOW = datetime(2026, 11, 26, 14, 0, tzinfo=timezone.utc)


def ts(day_offset=0, hour=10):
    return int((datetime(2026, 11, 26, hour, 0, tzinfo=timezone.utc) + timedelta(days=day_offset)).timestamp())


@pytest.fixture(autouse=True)
def isolated_archive(tmp_path, monkeypatch):
    monkeypatch.setattr(ha, "ARCHIVE_PATH", str(tmp_path / "headline_archive.json"))
    return tmp_path


def run(items, **kw):
    pushed_days, pushed_state = {}, []
    out = ha.run_archive(
        now=kw.pop("now", NOW),
        collect_fn=lambda n: (items, {"stub": len(items)}),
        push_day_fn=kw.pop("push_day_fn", lambda payload, path: pushed_days.__setitem__(path, json.loads(payload)) or True),
        push_state_fn=kw.pop("push_state_fn", lambda p: pushed_state.append(p) or True),
    )
    return out, pushed_days, pushed_state


def test_parse_rss_handles_rfc822_and_iso_dates_and_skips_bad_items():
    xml = b"""<rss><channel>
      <item><title>Stocks rally   on data</title><pubDate>Thu, 26 Nov 2026 09:55:44 GMT</pubDate></item>
      <item><title>Investing style</title><pubDate>2026-11-26 12:25:07</pubDate></item>
      <item><title></title><pubDate>Thu, 26 Nov 2026 09:55:44 GMT</pubDate></item>
      <item><title>No date</title></item>
      <item><title>Garbage date</title><pubDate>not a date</pubDate></item>
    </channel></rss>"""
    rows = ha.parse_rss(xml, "yahoo")
    assert [r["h"] for r in rows] == ["Stocks rally on data", "Investing style"]
    assert rows[0]["t"] == int(datetime(2026, 11, 26, 9, 55, 44, tzinfo=timezone.utc).timestamp())
    assert rows[1]["t"] == int(datetime(2026, 11, 26, 12, 25, 7, tzinfo=timezone.utc).timestamp())


def test_headlines_are_truncated_and_deduplicated_across_sources():
    long = "A" * 400
    items = [{"t": ts(), "h": ha._clean(long), "s": "a"}, {"t": ts(), "h": "Nasdaq tumbles as AI selloff returns", "s": "yahoo"},
             {"t": ts(0, 11), "h": "NASDAQ tumbles as AI selloff returns", "s": "finnhub"}]      # same headline, other case/source
    out, _, _ = run(items)
    saved = json.load(open(ha.ARCHIVE_PATH))
    assert len(saved["items"]) == 2 and out["new_items"] == 2
    assert max(len(i["h"]) for i in saved["items"]) == ha.MAX_HEADLINE_CHARS
    out2, _, _ = run(items)                              # a second pass with the same items adds nothing
    assert out2["new_items"] == 0


def test_completed_days_are_pushed_once_and_today_is_not():
    items = [{"t": ts(-1), "h": "Yesterday headline one", "s": "a"}, {"t": ts(-1, 15), "h": "Yesterday headline two", "s": "a"}, {"t": ts(), "h": "Today headline", "s": "a"}]
    out, pushed, state = run(items)
    assert list(pushed) == ["config/headlines/2026-11-25.json"]
    assert len(pushed["config/headlines/2026-11-25.json"]["items"]) == 2
    assert out["days_pushed"] == ["2026-11-25"] and state == [ha.ARCHIVE_PATH]
    out2, pushed2, _ = run(items)                        # unchanged count: no second push
    assert pushed2 == {} and out2["days_pushed"] == []


def test_late_item_for_an_archived_day_triggers_a_repush_with_all_items():
    first = [{"t": ts(-1), "h": "Day headline one", "s": "a"}]
    run(first)
    late = first + [{"t": ts(-1, 20), "h": "Late arriving headline", "s": "a"}]
    out, pushed, _ = run(late)
    assert len(pushed["config/headlines/2026-11-25.json"]["items"]) == 2


def test_failed_day_push_is_retried_next_run_and_nothing_is_pruned_before_it_lands():
    old = [{"t": ts(-9), "h": "Old headline", "s": "a"}]
    def boom(payload, path):
        raise RuntimeError("github down")
    out, _, _ = run(old, push_day_fn=boom, now=NOW + timedelta(days=0))
    # the 9-day-old item is older than the rolling window so it is dropped on arrival (never re-creates a pruned day)
    assert json.load(open(ha.ARCHIVE_PATH))["items"] == []
    inside = [{"t": ts(-3), "h": "Three days ago", "s": "a"}]
    out, _, _ = run(inside, push_day_fn=boom)
    assert out["errors"] and json.load(open(ha.ARCHIVE_PATH))["archived_counts"] == {}
    out2, pushed, _ = run(inside)
    assert out2["days_pushed"] == ["2026-11-23"] and pushed


def test_pruning_only_removes_days_already_archived_with_the_same_count():
    items = [{"t": ts(-6), "h": "Six days ago", "s": "a"}]
    run(items)
    later = NOW + timedelta(days=3)                      # now the item is 9 days old
    out, _, _ = run([], now=later)
    assert out["pruned"] == 1 and json.load(open(ha.ARCHIVE_PATH))["items"] == []


def test_a_failing_source_never_raises_and_is_reported(monkeypatch):
    monkeypatch.setenv("FINNHUB_API_KEY", "x")
    def bad(*a, **k):
        raise RuntimeError("down")
    monkeypatch.setattr(ha, "fetch_finnhub_general", bad)
    monkeypatch.setattr(ha, "fetch_finnhub_company", bad)
    monkeypatch.setattr(ha, "fetch_rss", lambda n, u: [{"t": ts(), "h": f"{n} headline", "s": n}] if n == "marketwatch" else (_ for _ in ()).throw(RuntimeError("x")))
    items, report = ha.collect(NOW)
    assert [i["s"] for i in items] == ["marketwatch"]
    assert report["finnhub_general"].startswith("error") and report["marketwatch"] == 1


def test_missing_finnhub_key_is_skipped_not_an_error(monkeypatch):
    monkeypatch.delenv("FINNHUB_API_KEY", raising=False)
    monkeypatch.setattr(ha, "fetch_rss", lambda n, u: [])
    _, report = ha.collect(NOW)
    assert report["finnhub"].startswith("skipped")


def test_run_archive_never_raises_even_when_the_collector_explodes():
    out = ha.run_archive(now=NOW, collect_fn=lambda n: (_ for _ in ()).throw(RuntimeError("boom")))
    assert out["errors"] and "boom" in out["errors"][0]


def test_state_file_is_registered_for_github_sync():
    from state_paths import STATE_FILES
    assert "config/headline_archive.json" in STATE_FILES
