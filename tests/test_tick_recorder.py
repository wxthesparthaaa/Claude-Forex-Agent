import gzip
import json
import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import tick_recorder as tr

NS_DAY1 = 1_800_000_000 * 10**9          # 2027-01-15 08:00:00 UTC
NS_DAY2 = NS_DAY1 + 24 * 3600 * 10**9


def _price(instrument="EUR_USD", bid="1.10000", ask="1.10012", tradeable=True, time="2027-01-15T08:00:00.123456789Z"):
    return json.dumps({"type": "PRICE", "time": time, "instrument": instrument, "tradeable": tradeable,
                       "bids": [{"price": bid, "liquidity": 1000000}], "asks": [{"price": ask, "liquidity": 1000000}]})


def test_parse_price_line_keeps_both_clocks_and_the_top_of_book():
    kind, row, oanda_time = tr.parse_stream_line(_price().encode(), 123)
    assert kind == "PRICE" and oanda_time == "2027-01-15T08:00:00.123456789Z"
    assert row == "123,2027-01-15T08:00:00.123456789Z,EUR_USD,1.10000,1.10012,1\n"


def test_parse_marks_untradeable_and_ignores_heartbeats_junk_and_empty_books():
    assert tr.parse_stream_line(_price(tradeable=False), 1)[1].endswith(",0\n")
    assert tr.parse_stream_line('{"type":"HEARTBEAT","time":"2027-01-15T08:00:05Z"}', 1)[0] == "HEARTBEAT"
    assert tr.parse_stream_line("not json", 1) is None
    assert tr.parse_stream_line("", 1) is None
    assert tr.parse_stream_line('{"type":"PRICE","time":"t","instrument":"X","bids":[],"asks":[]}', 1) is None


def test_clock_offset_is_receive_minus_oanda_time_in_milliseconds():
    recv_ns = NS_DAY1 + 40 * 10**6                                       # 40 ms after the OANDA stamp
    assert abs(tr.clock_offset_ms(recv_ns, "2027-01-15T08:00:00.000000000Z") - 40.0) < 1e-6
    assert abs(tr.clock_offset_ms(recv_ns, "2027-01-15T08:00:00Z") - 40.0) < 1e-6


def test_writer_rotates_at_utc_midnight_and_gzips_the_finished_day(tmp_path):
    w = tr.DayWriter(str(tmp_path))
    w.write(NS_DAY1, "a\n")
    w.write(NS_DAY2, "b\n")
    w.close()
    files = sorted(os.listdir(tmp_path))
    assert files == ["2027-01-15.csv.gz", "2027-01-16.csv"]
    with gzip.open(tmp_path / "2027-01-15.csv.gz", "rt") as f:
        assert f.read() == tr.HEADER + "a\n"
    assert (tmp_path / "2027-01-16.csv").read_text() == tr.HEADER + "b\n"


def test_writer_gzips_a_leftover_day_from_a_previous_run_on_startup(tmp_path):
    (tmp_path / "2026-01-10.csv").write_text(tr.HEADER + "x\n")
    tr.DayWriter(str(tmp_path))
    assert os.listdir(tmp_path) == ["2026-01-10.csv.gz"]


class _FakeResponse:
    def __init__(self, lines):
        self._lines = lines

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def raise_for_status(self):
        pass

    def iter_lines(self):
        return iter(self._lines)


def test_reconnect_closes_the_gap_and_records_it(tmp_path):
    stats = {"ticks": 0, "per_instrument": {}, "offsets": [], "connected": False, "last_recv_ns": None,
             "gap_start_ns": NS_DAY1, "gap_reason": "ConnectionError: wifi dropped", "backoff": 16.0}
    writer = tr.DayWriter(str(tmp_path))
    with patch("tick_recorder.requests.get", return_value=_FakeResponse([_price().encode()])):
        tr.stream_once("http://x", {}, {}, writer, stats, str(tmp_path))
    writer.close()
    assert stats["gap_start_ns"] is None and stats["backoff"] == 1.0
    gaps = (tmp_path / "gaps.csv").read_text().splitlines()
    assert gaps[0] == "start_utc,end_utc,seconds,reason" and "wifi dropped" in gaps[1]
    assert stats["ticks"] == 1 and stats["per_instrument"] == {"EUR_USD": 1}


def test_check_mode_writes_nothing(tmp_path):
    stats = {"ticks": 0, "per_instrument": {}, "offsets": [], "connected": False, "last_recv_ns": None,
             "gap_start_ns": None, "gap_reason": "", "backoff": 1.0}
    with patch("tick_recorder.requests.get", return_value=_FakeResponse([_price().encode()])):
        tr.stream_once("http://x", {}, {}, None, stats, str(tmp_path))
    assert os.listdir(tmp_path) == [] and stats["ticks"] == 1


def _ntp_reply(t2_unix: float, t3_unix: float) -> bytes:
    import struct

    def enc(t):
        n = t + tr.NTP_EPOCH_OFFSET
        secs = int(n)
        return struct.pack("!II", secs, int((n - secs) * 2**32))

    return bytes(32) + enc(t2_unix) + enc(t3_unix)


def test_ntp_offset_says_ahead_when_the_local_clock_runs_fast():
    # True time = local - 0.5s, 10 ms each way, server answers instantly.
    t1, t4 = 1_800_000_000.000, 1_800_000_000.020
    reply = _ntp_reply(t2_unix=t1 - 0.5 + 0.010, t3_unix=t1 - 0.5 + 0.010)
    ahead_ms, rtt_ms = tr.ntp_offset_from_packet(reply, t1, t4)
    assert abs(ahead_ms - 500.0) < 0.01 and abs(rtt_ms - 20.0) < 0.01


def test_ntp_offset_says_behind_when_the_local_clock_runs_slow():
    t1, t4 = 1_800_000_000.000, 1_800_000_000.020
    reply = _ntp_reply(t2_unix=t1 + 0.3 + 0.010, t3_unix=t1 + 0.3 + 0.010)
    ahead_ms, _ = tr.ntp_offset_from_packet(reply, t1, t4)
    assert abs(ahead_ms + 300.0) < 0.01


def test_live_line_updates_in_place_and_a_log_line_starts_on_a_fresh_line(tmp_path, capsys):
    tr.live_line("recording OK -- 10 ticks")
    tr.log(str(tmp_path), "disconnected (test)")
    out = capsys.readouterr().out
    assert out.startswith("\rrecording OK -- 10 ticks")
    assert "\ndisconnected".replace("\n", "") not in out.split("\n")[0]      # live line is on its own line
    assert out.split("\n")[1].endswith("disconnected (test)")


def test_stream_shows_the_live_line_only_when_recording(tmp_path, capsys):
    stats = {"ticks": 0, "per_instrument": {}, "offsets": [], "connected": False, "last_recv_ns": None,
             "gap_start_ns": None, "gap_reason": "", "backoff": 1.0}
    writer = tr.DayWriter(str(tmp_path))
    with patch("tick_recorder.requests.get", return_value=_FakeResponse([_price().encode()])):
        tr.stream_once("http://x", {}, {}, writer, stats, str(tmp_path))
    writer.close()
    assert "recording OK -- 1 ticks this session" in capsys.readouterr().out


# ---------------------------------------------------------------- self-check + Telegram alerts
SEC = 10**9


def _health_stats(**kw):
    base = {"gap_start_ns": None, "gap_reason": "ConnectionError: wifi", "last_price_ns": None,
            "alerted_outage": False, "alerted_silence": False}
    base.update(kw)
    return base


def test_short_blips_are_not_announced_but_a_minute_long_outage_is_announced_once():
    stats = _health_stats(gap_start_ns=NS_DAY1)
    assert tr.evaluate_health(stats, NS_DAY1 + 30 * SEC, True) == []
    msgs = tr.evaluate_health(stats, NS_DAY1 + 61 * SEC, True)
    assert len(msgs) == 1 and "PAUSED" in msgs[0] and "wifi" in msgs[0] and "saved" in msgs[0]
    assert tr.evaluate_health(stats, NS_DAY1 + 200 * SEC, True) == []          # not repeated


def test_silent_stream_alerts_only_while_the_market_is_open_and_recovers_with_a_message():
    stats = _health_stats(last_price_ns=NS_DAY1)
    assert tr.evaluate_health(stats, NS_DAY1 + 10 * 60 * SEC, False) == []      # weekend: quiet is normal
    assert tr.evaluate_health(stats, NS_DAY1 + 10 * 60 * SEC, None) == []       # unknown market state: stay quiet
    msgs = tr.evaluate_health(stats, NS_DAY1 + 10 * 60 * SEC, True)
    assert len(msgs) == 1 and "SILENT" in msgs[0]
    assert tr.evaluate_health(stats, NS_DAY1 + 11 * 60 * SEC, True) == []
    stats["last_price_ns"] = NS_DAY1 + 12 * 60 * SEC                            # a price arrives
    recovered = tr.evaluate_health(stats, NS_DAY1 + 12 * 60 * SEC + 5 * SEC, True)
    assert len(recovered) == 1 and "flowing again" in recovered[0]


def test_resume_message_only_for_announced_or_long_gaps_and_names_the_missing_minutes():
    short = _health_stats()
    assert tr.resume_message(short, NS_DAY1, NS_DAY1 + 3 * SEC) is None
    long_gap = _health_stats(frozen_note="this PC was frozen or asleep for about 9 min")
    msg = tr.resume_message(long_gap, NS_DAY1, NS_DAY1 + 600 * SEC)
    assert "RESUMED" in msg and "min)" in msg and "asleep" in msg and "wifi" in msg
    announced = _health_stats(alerted_outage=True)
    assert tr.resume_message(announced, NS_DAY1, NS_DAY1 + 5 * SEC) is not None


def test_reconnect_after_a_long_gap_sends_the_resume_alert_and_clears_the_flags(tmp_path):
    sent = []
    stats = {"ticks": 0, "per_instrument": {}, "offsets": [], "connected": False, "last_recv_ns": None,
             "gap_start_ns": NS_DAY1 - 300 * SEC, "gap_reason": "ReadTimeout", "backoff": 32.0,
             "alerted_outage": True, "frozen_note": None, "notify": sent.append}
    writer = tr.DayWriter(str(tmp_path))
    with patch("tick_recorder.requests.get", return_value=_FakeResponse([_price().encode()])):
        tr.stream_once("http://x", {}, {}, writer, stats, str(tmp_path))
    writer.close()
    assert len(sent) == 1 and "RESUMED" in sent[0]
    assert stats["alerted_outage"] is False and stats["gap_start_ns"] is None


def test_notifier_keeps_an_alert_and_retries_until_the_internet_is_back():
    import time as _time
    attempts = []

    def flaky(text):
        attempts.append(text)
        if len(attempts) < 3:
            raise OSError("no internet")

    n = tr.Notifier(sender=flaky, retry_seconds=0.01)
    n.start()
    n.send("outage alert")
    deadline = _time.time() + 3
    while len(attempts) < 3 and _time.time() < deadline:
        _time.sleep(0.02)
    n.stop()
    assert attempts == ["outage alert"] * 3


def test_notifier_disables_itself_when_telegram_is_not_configured():
    import time as _time

    def unconfigured(text):
        raise FileNotFoundError("telegram_config.properties")

    n = tr.Notifier(sender=unconfigured, retry_seconds=0.01)
    n.start()
    n.send("hello")
    _time.sleep(0.3)
    n.stop()
    assert n.enabled is False


def test_writer_flush_puts_buffered_rows_on_disk_immediately(tmp_path):
    w = tr.DayWriter(str(tmp_path))
    w.write(NS_DAY1, "row-1\n")
    assert "row-1" not in (tmp_path / "2027-01-15.csv").read_text()   # still buffered
    w.flush()
    assert "row-1" in (tmp_path / "2027-01-15.csv").read_text()
    w.close()


def test_a_same_day_restart_does_not_gzip_todays_file_and_rotation_never_overwrites_a_part(tmp_path):
    # Real bug found the first time the recorder was restarted mid-day: startup gzipped TODAY's csv, the new run
    # started a fresh csv, and the UTC-midnight rotation would then have overwritten the first part's .gz.
    import gzip as _gzip
    import time as _time
    today = tr.utc_day(_time.time_ns())
    part1 = tmp_path / f"{today}.csv"
    part1.write_text(tr.HEADER + "first-run\n")
    w = tr.DayWriter(str(tmp_path))
    assert part1.exists() and not (tmp_path / f"{today}.csv.gz").exists()   # today's file is left alone

    with _gzip.open(tmp_path / "2026-01-11.csv.gz", "wt") as f:              # a part already packed by an earlier run
        f.write(tr.HEADER + "part-one\n")
    (tmp_path / "2026-01-11.csv").write_text(tr.HEADER + "part-two\n")
    w.compress_finished(current=today)

    names = sorted(n for n in os.listdir(tmp_path) if n.startswith("2026-01-11"))
    assert names == ["2026-01-11.1.csv.gz", "2026-01-11.csv.gz"]
    with _gzip.open(tmp_path / "2026-01-11.csv.gz", "rt") as f:
        assert "part-one" in f.read()                                        # not overwritten
    with _gzip.open(tmp_path / "2026-01-11.1.csv.gz", "rt") as f:
        assert "part-two" in f.read()
