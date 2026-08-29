import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from profit_decay_exit import simulate_trade_with_decay_exit

BARS_PER_HOUR = 4  # 15m candles


def _candle(close, high=None, low=None, open_=None):
    o = open_ if open_ is not None else close
    h = high if high is not None else max(o, close)
    l = low if low is not None else min(o, close)
    return {"mid": {"o": f"{o}", "h": f"{h}", "l": f"{l}", "c": f"{close}"}}


def _flat_run(n, close):
    """n bars that all close at `close`, safely away from any test's SL/TP."""
    return [_candle(close) for _ in range(n)]


def test_sl_hit_before_the_first_checkpoint_is_a_normal_loss_not_a_time_cut():
    candles = [_candle(100)] + _flat_run(3, 100) + [_candle(89, high=100, low=89)] + _flat_run(10, 100)
    result = simulate_trade_with_decay_exit(candles, 0, "LONG", 100, 90, 120, bars_per_hour=BARS_PER_HOUR)
    assert result.outcome == "LOSS"
    assert result.exit_index == 4


def test_tp_hit_before_the_first_checkpoint_is_a_normal_win_not_a_time_cut():
    candles = [_candle(100)] + _flat_run(3, 100) + [_candle(121, high=121, low=100)] + _flat_run(10, 100)
    result = simulate_trade_with_decay_exit(candles, 0, "LONG", 100, 90, 120, bars_per_hour=BARS_PER_HOUR)
    assert result.outcome == "WIN"
    assert result.exit_index == 4


def test_negative_at_the_2hr_checkpoint_cuts_immediately():
    # entry 100, stop 90 (risk=10) -- 8 bars (2h) drifting down to 97 (pnl_r = -0.3)
    candles = [_candle(100)] + [_candle(99), _candle(98), _candle(97.5), _candle(97),
                                  _candle(97), _candle(97), _candle(97), _candle(97)]
    result = simulate_trade_with_decay_exit(candles, 0, "LONG", 100, 90, 120, bars_per_hour=BARS_PER_HOUR)
    assert result.outcome == "TIME_CUT_LOSS"
    assert result.exit_index == 8
    assert result.exit_price == 97.0
    assert round(result.r_multiple, 2) == -0.30


def test_positive_at_2hr_that_declines_by_3hr_cuts_at_3hr_matching_the_users_own_example():
    # User's own numbers, proportionally: 2hr checkpoint is the better
    # reading (analogous to "$50"), 3hr is worse but still positive
    # (analogous to "$45") -- 45 < 50 means cancel, even though still
    # a winning trade in absolute terms.
    candles = (
        [_candle(100)]
        + [_candle(101), _candle(102), _candle(103), _candle(104), _candle(105), _candle(105), _candle(105)]
        + [_candle(105)]          # bar 8 = 2hr checkpoint: pnl_r = (105-100)/10 = 0.5 -- positive, continue
        + [_candle(105), _candle(104.8), _candle(104.6)]
        + [_candle(104.5)]        # bar 12 = 3hr checkpoint: pnl_r = 0.45 < 0.5 -- decay, cut here
    )
    result = simulate_trade_with_decay_exit(candles, 0, "LONG", 100, 90, 120, bars_per_hour=BARS_PER_HOUR)
    assert result.outcome == "TIME_DECAY"
    assert result.exit_index == 12
    assert result.exit_price == 104.5
    assert round(result.r_multiple, 3) == 0.45  # still a partial winner -- cut on the DECLINE, not on going negative


def test_decay_check_is_against_the_immediately_prior_checkpoint_not_the_peak():
    # 2hr=+0.3R, 3hr=+0.6R (rose -- continue, new baseline is 0.6, NOT 0.3),
    # 4hr=+0.5R -- below 3hr's 0.6 but still above 2hr's 0.3 and above the
    # very first checkpoint. A peak-based trailing stop would let this
    # ride (still above the 0.3 peak-adjacent floor); this rule cuts it
    # anyway because it strictly compares to the immediately PRIOR
    # checkpoint, exactly as specified, not the best-ever reading.
    candles = (
        [_candle(100)]
        + _flat_run(7, 100) + [_candle(103)]      # bars 1-8: bar 8 (2hr) closes 103 -> pnl_r=0.3
        + _flat_run(3, 103) + [_candle(106)]      # bars 9-12: bar 12 (3hr) closes 106 -> pnl_r=0.6 (rose, continue)
        + _flat_run(3, 106) + [_candle(105)]      # bars 13-16: bar 16 (4hr) closes 105 -> pnl_r=0.5 (< 0.6 -> cut)
    )
    result = simulate_trade_with_decay_exit(candles, 0, "LONG", 100, 90, 120, bars_per_hour=BARS_PER_HOUR)
    assert result.outcome == "TIME_DECAY"
    assert result.exit_index == 16
    assert round(result.r_multiple, 2) == 0.50


def test_steadily_rising_trade_reaches_take_profit_normally_decay_rule_never_fires():
    candles = (
        [_candle(100)]
        + _flat_run(7, 100) + [_candle(102)]   # 2hr: pnl_r=0.2, positive, continue
        + _flat_run(3, 102) + [_candle(108)]   # 3hr: pnl_r=0.8, rose, continue
        + _flat_run(3, 108) + [_candle(121, high=121, low=108)]  # TP hit before the next checkpoint
    )
    result = simulate_trade_with_decay_exit(candles, 0, "LONG", 100, 90, 120, bars_per_hour=BARS_PER_HOUR)
    assert result.outcome == "WIN"


def test_1hr_loss_check_cuts_a_full_hour_earlier_than_the_default():
    # Same shape as test_negative_at_the_2hr_checkpoint_cuts_immediately
    # but the loss check now fires at hour 1 -- confirms the trade is
    # cut a full hour sooner given the same price path.
    candles = [_candle(100)] + [_candle(99), _candle(98), _candle(97.5), _candle(97)] + _flat_run(10, 97)
    result = simulate_trade_with_decay_exit(candles, 0, "LONG", 100, 90, 120,
                                              bars_per_hour=BARS_PER_HOUR, loss_check_hour=1, decay_start_hour=3)
    assert result.outcome == "TIME_CUT_LOSS"
    assert result.exit_index == 4  # 1hr mark = bar 4, not bar 8


def test_hour_2_is_a_silent_baseline_when_decay_watching_starts_at_hour_3():
    # loss_check_hour=1 (positive, so it survives), decay_start_hour=3:
    # hour 2 must NOT trigger a cut even though it's lower than hour 1 --
    # it's purely recorded as the baseline hour 3 gets compared against.
    candles = (
        [_candle(100)]
        + _flat_run(3, 100) + [_candle(105)]   # bar 4 = 1hr: pnl_r=0.5, positive, survives (loss check only)
        + _flat_run(3, 105) + [_candle(102)]   # bar 8 = 2hr: pnl_r=0.2, LOWER than 0.5 -- must NOT cut here
        + _flat_run(3, 102) + [_candle(103)]   # bar 12 = 3hr: pnl_r=0.3, HIGHER than hour 2's 0.2 -- continue
    )
    result = simulate_trade_with_decay_exit(candles, 0, "LONG", 100, 90, 120,
                                              bars_per_hour=BARS_PER_HOUR, loss_check_hour=1, decay_start_hour=3)
    assert result.outcome == "OPEN_AT_END"  # ran out of data -- never cut, confirming hour 2 didn't trigger


def test_hour_3_decay_check_compares_against_hour_2_not_hour_1():
    # loss_check_hour=1, decay_start_hour=3: hour 1=0.5 (survives),
    # hour 2=0.2 (recorded silently, not compared), hour 3=0.15 (LOWER
    # than hour 2's 0.2 -- cut). If the comparison baseline were hour 1
    # instead of hour 2, 0.15 < 0.5 would ALSO cut, so this alone
    # wouldn't prove which baseline is used -- the discriminating case
    # is covered by the "must NOT cut at hour 2" test above; this one
    # confirms the hour-3 cut fires against the correct (hour 2) value.
    candles = (
        [_candle(100)]
        + _flat_run(3, 100) + [_candle(105)]   # bar 4 = 1hr: pnl_r=0.5
        + _flat_run(3, 105) + [_candle(102)]   # bar 8 = 2hr: pnl_r=0.2 (silent baseline)
        + _flat_run(3, 102) + [_candle(101.5)]  # bar 12 = 3hr: pnl_r=0.15 < 0.2 -- cut
    )
    result = simulate_trade_with_decay_exit(candles, 0, "LONG", 100, 90, 120,
                                              bars_per_hour=BARS_PER_HOUR, loss_check_hour=1, decay_start_hour=3)
    assert result.outcome == "TIME_DECAY"
    assert result.exit_index == 12
    assert round(result.r_multiple, 3) == 0.15


def test_short_direction_is_mirrored_correctly():
    # SHORT: entry 100, stop 110 (risk=10), profit means price falling.
    # 2hr close=97 -> pnl_r=(100-97)/10=0.3 (positive, continue).
    # 3hr close=98 -> pnl_r=0.2 < 0.3 -- decay, cut.
    candles = (
        [_candle(100)]
        + _flat_run(7, 100) + [_candle(97)]
        + _flat_run(3, 97) + [_candle(98)]
    )
    result = simulate_trade_with_decay_exit(candles, 0, "SHORT", 100, 110, 70, bars_per_hour=BARS_PER_HOUR)
    assert result.outcome == "TIME_DECAY"
    assert result.exit_index == 12
    assert round(result.r_multiple, 2) == 0.20
