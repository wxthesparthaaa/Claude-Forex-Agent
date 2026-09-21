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
    (tmp_path / "2027-01-10.csv").write_text(tr.HEADER + "x\n")
    tr.DayWriter(str(tmp_path))
    assert os.listdir(tmp_path) == ["2027-01-10.csv.gz"]


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
