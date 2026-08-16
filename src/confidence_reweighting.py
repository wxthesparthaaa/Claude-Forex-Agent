"""
Weekly confidence-weight reweighting from accumulated live trade
outcomes -- the "tunable via the Friday self-reflection process" idea
confidence_score.py's own docstring named as the goal from day one, but
never actually built. Runs alongside apply_self_improvement in
scheduled_jobs.run_friday_reflection, same weekly touchpoint.

Deliberately conservative given this project's own 413-day backtest
(2026-08-14/15), which found that short samples routinely look like
real edges and aren't: a component's weight only moves if there's
enough data to make the comparison meaningful, moves by a small capped
amount per week, and is bounded so no single component can ever
dominate or vanish from the blend. This is a heuristic nudge toward
"which inputs have actually correlated with winning," not a rigorous
statistical test -- every adjustment is explained in the Friday message
so it's inspectable, not a black box.
"""
from __future__ import annotations

from dataclasses import asdict

from confidence_score import ConfidenceWeights

# Need at least this many trades in BOTH the >=70 and <70 buckets for a
# component before its weight is touched at all -- below this, an
# observed difference is as likely to be noise as signal.
MIN_SAMPLES_PER_BUCKET = 15
HIGH_THRESHOLD = 70.0
# Max weight change in a single weekly pass, and the floor/ceiling no
# component's weight can cross -- keeps this a slow nudge over many
# consistent weeks, not a lever a single good/bad stretch can swing
# hard, and guarantees the blend never collapses onto one component.
MAX_WEIGHT_STEP = 0.03
MIN_WEIGHT = 0.05
MAX_WEIGHT = 0.60
# How strongly a win-rate lift (high bucket vs low bucket) translates
# into a weight nudge -- e.g. a 20-percentage-point lift produces the
# full MAX_WEIGHT_STEP; smaller lifts produce proportionally smaller
# nudges. A heuristic scale, not a significance test.
ADJUSTMENT_SENSITIVITY = 0.15

COMPONENTS = ("breadth", "rsi", "candlestick", "news")


def _bucket_win_rates(journal_entries: list, component: str):
    """(high_win_rate_pct, high_n, low_win_rate_pct, low_n) for one
    component across all closed, decisively-won-or-lost trades that
    have a recorded, genuinely-available score for it. None if either
    bucket is below MIN_SAMPLES_PER_BUCKET."""
    high_wins = high_n = low_wins = low_n = 0
    for e in journal_entries:
        if e.get("status") == "OPEN":
            continue
        pnl = e.get("realized_pnl")
        if pnl is None or pnl == 0:
            continue  # breakeven or unrecoverable -- excluded, matches win_loss_counts
        score = (e.get("confidence_components") or {}).get(component)
        if score is None:
            continue  # not journaled (pre-2026-08-13 trade) or this component had no reading
        # confidence_score.py fills a missing input with a neutral 50.0
        # so the blended confidence_pct always has a number to work
        # with -- but that synthetic 50.0 is not evidence the component
        # was genuinely weak, and must not be read as such here (e.g.
        # "news" is never available for commodity trades). Entries
        # journaled before this field existed have no availability
        # record at all -- treated as available, matching the prior
        # behavior for that older data rather than silently discarding it.
        available = (e.get("confidence_components_available") or {}).get(component, True)
        if not available:
            continue
        won = pnl > 0
        if score >= HIGH_THRESHOLD:
            high_n += 1
            high_wins += 1 if won else 0
        else:
            low_n += 1
            low_wins += 1 if won else 0

    if high_n < MIN_SAMPLES_PER_BUCKET or low_n < MIN_SAMPLES_PER_BUCKET:
        return None
    return (100 * high_wins / high_n, high_n, 100 * low_wins / low_n, low_n)


def reweight_confidence_components(journal_entries: list, current_weights: ConfidenceWeights):
    """Returns (new_weights, explanation_lines). new_weights always sums
    to 1.0, the invariant compute_confidence's blend assumes."""
    raw = dict(asdict(current_weights))
    bucket_results = {}

    for component in COMPONENTS:
        result = _bucket_win_rates(journal_entries, component)
        bucket_results[component] = result
        if result is None:
            continue
        high_wr, _, low_wr, _ = result
        lift = (high_wr - low_wr) / 100  # fraction, e.g. 0.16 for a 16-point lift
        adjustment = max(-MAX_WEIGHT_STEP, min(MAX_WEIGHT_STEP, lift * ADJUSTMENT_SENSITIVITY))
        raw[component] = max(MIN_WEIGHT, min(MAX_WEIGHT, raw[component] + adjustment))

    total = sum(raw.values())
    normalized = {k: v / total for k, v in raw.items()}

    old = asdict(current_weights)
    lines = []
    for component in COMPONENTS:
        result = bucket_results[component]
        if result is None:
            lines.append(f"{component}: not enough data yet to reassess "
                         f"(need {MIN_SAMPLES_PER_BUCKET}+ trades on both sides of {HIGH_THRESHOLD:.0f})")
            continue
        high_wr, high_n, low_wr, low_n = result
        old_w, new_w = old[component], normalized[component]
        if abs(new_w - old_w) < 0.005:
            lines.append(f"{component}: unchanged at {old_w:.2f} ({high_wr:.0f}% win rate when "
                         f"≥{HIGH_THRESHOLD:.0f} vs {low_wr:.0f}% when <{HIGH_THRESHOLD:.0f}, "
                         f"n={high_n}/{low_n} -- no meaningful difference)")
        else:
            lines.append(f"{component} {old_w:.2f}→{new_w:.2f} ({high_wr:.0f}% win rate when "
                         f"≥{HIGH_THRESHOLD:.0f} vs {low_wr:.0f}% when <{HIGH_THRESHOLD:.0f}, "
                         f"n={high_n}/{low_n})")

    return ConfidenceWeights(**normalized), lines
