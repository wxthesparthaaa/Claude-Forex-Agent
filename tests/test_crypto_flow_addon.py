import pytest
import os
import sys
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import crypto_flow_addon as cf
import dashboard_state as ds
import trade_journal as tj
from autopilot import PhaseState

TUESDAY_0130 = datetime(2026, 10, 27, 1, 30, tzinfo=timezone.utc)  # market open, inside the 01:00-04:00 UTC window


def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(tj, "STATE_DIR", str(tmp_path))
    monkeypatch.setattr(tj, "JOURNAL_PATH", str(tmp_path / "trade_journal.json"))
    monkeypatch.setattr(ds, "STATE_DIR", str(tmp_path))
    monkeypatch.setattr(ds, "STATE_PATH", str(tmp_path / "dashboard_state.json"))
    monkeypatch.setattr(cf, "CRYPTO_FLOW_LOG_PATH", str(tmp_path / "crypto_flow_log.json"))


def _state(enabled=True):
    state = ds.default_state()
    state.phase_state = asdict(PhaseState(phase="autopilot"))
    state.crypto_flow_enabled = enabled
    ds.save_state(state)


# ---- signal maths

def _ms(day: str) -> int:
    return int(datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() * 1000)


def _days(n, end="2026-10-26"):
    last = datetime.strptime(end, "%Y-%m-%d")
    return [(last - timedelta(days=n - 1 - i)).strftime("%Y-%m-%d") for i in range(n)]


def test_z_for_day_uses_only_prior_days_and_needs_history():
    days = _days(200)
    series = {d: float(i % 7) for i, d in enumerate(days)}
    series[days[-1]] = 100.0  # the day being scored must not leak into its own baseline
    z = cf.z_for_day(series, days[-1])
    assert z is not None and z > 5
    assert cf.z_for_day({d: 1.0 for d in days[:50]}, days[49]) is None  # fewer than MIN_DAYS prior
    assert cf.z_for_day({d: 1.0 for d in days}, days[-1]) is None  # no variation
    assert cf.z_for_day(series, "2030-01-01") is None  # day absent


def test_z_for_day_window_is_last_250_prior_entries():
    days = _days(400)
    series = {d: 0.0 for d in days[:150]}
    series.update({d: float(i % 5) for i, d in enumerate(days[150:])})
    z_full = cf.z_for_day(series, days[-1])
    series_truncated = {d: v for d, v in series.items() if d >= days[-251]}  # exactly the 250 prior entries + the day
    assert abs(z_full - cf.z_for_day(series_truncated, days[-1])) < 1e-12


def test_daily_funding_requires_three_settlements():
    d1, d2 = _ms("2026-10-20"), _ms("2026-10-21")
    rows = [(d1 + h * 3600_000, 0.0001) for h in (0, 8, 16)] + [(d2, 0.0005), (d2 + 8 * 3600_000, 0.0005)]
    out = cf.daily_funding(rows)
    assert list(out) == ["2026-10-20"] and abs(out["2026-10-20"] - 0.0001) < 1e-12


def test_taker_share_and_premium_exclude_the_incomplete_day():
    kl = [(_ms("2026-10-24"), 100.0, 60.0, 50000.0), (_ms("2026-10-25"), 100.0, 40.0, 51000.0)]
    assert cf.daily_taker_share(kl, "2026-10-25") == {"2026-10-24": 0.6}
    cb = [(_ms("2026-10-24") // 1000, 50500.0), (_ms("2026-10-25") // 1000, 51000.0)]
    prem = cf.daily_premium(cb, kl, "2026-10-25")
    assert list(prem) == ["2026-10-24"] and prem["2026-10-24"] > 0  # Coinbase above Binance


def test_composite_reports_which_gauge_is_missing():
    out = cf.composite_for_day("2026-10-26", [], [], [])
    assert "error" in out and "funding" in out["error"]


def _synthetic_inputs(last_day_scale=1.0, quiet=False):
    days = _days(300)
    funding, kl, cb = [], [], []
    for i, d in enumerate(days):
        wiggle = (i % 11 - 5) * 1e-5
        spike = 1.0 if d != days[-1] else last_day_scale
        funding += [(_ms(d) + h * 3600_000, (1e-4 + wiggle) * spike) for h in (0, 8, 16)]
        kl.append((_ms(d), 1000.0, 500.0 + (i % 13) * 3 * spike, 50000.0))
        cb.append((_ms(d) // 1000, 50000.0 * (1 + 1e-4 * (i % 9 - 4) * spike)))
    if quiet:  # the last completed day sits exactly at each gauge's own average
        funding[-3:] = [(t, 1e-4) for t, _ in funding[-3:]]
        kl[-1] = (kl[-1][0], 1000.0, 518.0, 50000.0)
        cb[-1] = (cb[-1][0], 50000.0)
    return days[-1], funding, kl, cb


def test_composite_is_the_mean_of_the_three_and_signs_the_direction():
    day, funding, kl, cb = _synthetic_inputs(last_day_scale=1.0)
    out = cf.composite_for_day(day, funding, kl, cb)
    assert abs(out["composite"] - (out["zf"] + out["zt"] + out["zp"]) / 3) < 1e-12
    assert cf.direction_for(1.0) == "LONG" and cf.direction_for(-1.0) == "SHORT" and cf.direction_for(0.99) is None


# ---- sizing

def test_size_units_floors_to_the_unit_step_and_signs_by_direction():
    rate = Decimal("1.3")
    long_units = cf.size_units("LONG", 3, "0.001", "1000", 80000.0, 76000.0, 500.0, rate)
    short_units = cf.size_units("SHORT", 3, "0.001", "1000", 80000.0, 84000.0, 500.0, rate)
    assert long_units == 0.096 and short_units == -0.096  # 500 / (4000 * 1.3) = 0.0961 -> 0.096
    assert long_units * 4000 * 1.3 <= 500.0


def test_size_units_skips_when_minimum_size_overshoots_the_budget():
    assert cf.size_units("LONG", 3, "0.001", "1000", 80000.0, 76000.0, 1.0, Decimal("1.3")) == 0.0  # 0.001 BTC risks 5.2 > 1.5 x 1
    assert cf.size_units("LONG", 2, "0.01", "10000", 2400.0, 2300.0, 500.0, Decimal("1.3")) > 0


def test_size_units_respects_the_maximum_order_size():
    assert abs(cf.size_units("LONG", 2, "0.01", "5", 60.0, 55.0, 1000.0, Decimal("1"))) <= 5


def test_risk_fraction_is_a_quarter():
    assert cf.RISK_FRACTION == 0.25


# ---- log

def test_day_already_evaluated_needs_every_instrument_and_ignores_unavailable():
    names = list(cf.INSTRUMENTS)
    entries = [{"day": "d", "instrument": i, "result": "no_signal"} for i in names[:3]]
    assert not cf.day_already_evaluated(entries, "d")
    entries.append({"day": "d", "instrument": names[3], "result": "data_unavailable"})
    assert not cf.day_already_evaluated(entries, "d")
    entries.append({"day": "d", "instrument": names[3], "result": "skipped"})
    assert cf.day_already_evaluated(entries, "d")


# ---- the tick

def _fetchers(inputs):
    day, funding, kl, cb = inputs
    return {"funding": lambda s, n: funding, "klines": lambda s, n: kl, "coinbase": lambda p, n: cb}


def test_disabled_does_nothing(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    _state(enabled=False)
    with patch.object(cf, "_open_position") as op:
        assert cf.check_crypto_flow(client=object(), now=TUESDAY_0130, fetchers=_fetchers(_synthetic_inputs())) == []
    op.assert_not_called()
    assert cf.load_log() == []


def test_outside_the_entry_window_does_nothing(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    _state()
    with patch.object(cf, "_open_position") as op:
        cf.check_crypto_flow(client=object(), now=TUESDAY_0130.replace(hour=9), fetchers=_fetchers(_synthetic_inputs()))
    op.assert_not_called()
    assert cf.load_log() == []


def test_strong_day_opens_long_once_and_is_not_repeated(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    _state()
    inputs = _synthetic_inputs(last_day_scale=6.0)  # every gauge unusually high on the last completed day
    with patch.object(cf, "_open_position", return_value=True) as op:
        opened = cf.check_crypto_flow(client=object(), now=TUESDAY_0130, fetchers=_fetchers(inputs))
        again = cf.check_crypto_flow(client=object(), now=TUESDAY_0130 + timedelta(minutes=5), fetchers=_fetchers(inputs))
    assert sorted(opened) == sorted(cf.INSTRUMENTS) and again == []
    assert op.call_count == len(cf.INSTRUMENTS) and all(c.args[2] == "LONG" for c in op.call_args_list)
    assert {e["result"] for e in cf.load_log()} == {"opened"}


def test_quiet_day_records_no_signal(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    _state()
    with patch.object(cf, "_open_position") as op:
        cf.check_crypto_flow(client=object(), now=TUESDAY_0130, fetchers=_fetchers(_synthetic_inputs(quiet=True)))
    op.assert_not_called()
    assert {e["result"] for e in cf.load_log()} == {"no_signal"}


def test_unreachable_source_trades_nothing_and_alerts_once(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    _state()

    def boom(*a):
        raise OSError("HTTP 451")
    fetchers = {"funding": boom, "klines": lambda s, n: [], "coinbase": lambda p, n: []}
    with patch.object(cf, "_open_position") as op, patch.object(cf, "send_message") as msg:
        cf.check_crypto_flow(client=object(), now=TUESDAY_0130, fetchers=fetchers)
        cf.check_crypto_flow(client=object(), now=TUESDAY_0130 + timedelta(minutes=5), fetchers=fetchers)
    op.assert_not_called()
    assert msg.call_count == 1 and "could not build" in msg.call_args.args[0]
    assert not cf.day_already_evaluated(cf.load_log(), "2026-10-26")  # will retry within the window


def test_position_held_24h_is_closed_when_market_open(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    _state()
    opened_at = (TUESDAY_0130 - timedelta(hours=25)).isoformat()
    entry = {"trade_id": "t1", "instrument": "BTC_USD", "direction": "LONG", "status": "OPEN",
             "experiment_tag": cf.CRYPTO_FLOW_TAG, "opened_at": opened_at}
    with patch.object(cf, "open_entries", return_value=[entry]), patch.object(cf, "load_journal", return_value=[entry]), \
         patch.object(cf, "_force_close") as fc:
        cf.check_crypto_flow(client=object(), now=TUESDAY_0130.replace(hour=9), fetchers=_fetchers(_synthetic_inputs()))
    fc.assert_called_once()


def test_funding_falls_back_to_second_host_when_primary_refuses(monkeypatch):
    calls = []

    def fake_get(url):
        calls.append(url)
        if url.startswith("https://fapi.binance.com"):
            raise OSError("HTTP Error 451")
        return [{"fundingTime": 1793001600000, "fundingRate": "0.0001"}]
    monkeypatch.setattr(cf, "_get_json", fake_get)
    rows = cf.fetch_funding("BTCUSDT", TUESDAY_0130)
    assert rows == [(1793001600000, 0.0001)]
    assert [u.split("/fapi/v1")[0] for u in calls] == list(cf.FUNDING_HOSTS)


def test_funding_raises_when_every_host_refuses(monkeypatch):
    def fake_get(url):
        raise OSError("HTTP Error 451")
    monkeypatch.setattr(cf, "_get_json", fake_get)
    with pytest.raises(OSError):
        cf.fetch_funding("BTCUSDT", TUESDAY_0130)
