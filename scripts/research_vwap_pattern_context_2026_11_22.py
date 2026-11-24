"""
Research, 2026-11-22. PRE-REGISTERED before any result was seen. User question after the ceiling filter: what about the FLOOR (support), double/triple bottoms and flag / channel structure in the
look-back? The ceiling filter only looked at ONE thing (the nearest swing high above a long / swing low below a short). This tests three more pieces of chart context on the same 372 live VWAP Scalp
entries (2026-10-14..11-17; real one-minute bid/ask after entry, mid minutes for the 75 minutes before entry).
Everything is computed from the 60 minutes before the entry minute; swings are 3-minute local extremes confirmed before entry (as in research_vwap_swing_tp_2026_11_22.py). A SHORT is handled by
mirroring prices (negating them), so the text below is written for a LONG.
  A1 FLOOR INSIDE THE STOP: the nearest confirmed swing LOW below the entry lies ABOVE the stop (a floor sits between entry and stop, so price has to break it before stopping us out). Hypothesis: better.
  A2 DOUBLE / TRIPLE BOTTOM: two or more confirmed swing lows below the entry, at least 5 minutes apart, within 0.5 x the stop distance of each other (equal lows). Hypothesis: better.
  B  DESCENDING STRUCTURE (a falling flag / channel): the last two confirmed swing highs are falling AND the last two confirmed swing lows are falling (lower highs and lower lows; at least two of
     each needed). A long fade then runs against a continuing downtrend. Hypothesis: worse (so the filter would SKIP these).
K = 3, alpha 0.05/3 = 0.0167, one-sided. For A1/A2 the filter KEEPS flagged entries; for B it KEEPS the unflagged ones.
OUTCOME: R in units of the stop distance, TP at the VWAP target (x1), also at the live 3x. Placebo: keep the same number of trades chosen at random (10,000 draws).
VERDICT RULE: SUPPORTED only if the KEPT set has mean R > 0 at TP x1 with one-sided p < 0.0167 AND kept beats dropped in BOTH the pre and post halves AND the kept mean is above the 97.5th percentile
of the random placebo. Otherwise NOT SUPPORTED. Post-hoc caveat as before (entries after 17+ refinements).
Usage: venv/Scripts/python.exe scripts/research_vwap_pattern_context_2026_11_22.py SCRATCH_DIR
"""
import math
import os
import random
import statistics
import sys
from datetime import timedelta, timezone

sys.path.insert(0, os.path.dirname(__file__))
import research_vwap_swing_tp_2026_11_22 as S  # noqa: E402


def swings(mins, key_hi="h", key_lo="l"):
    """Confirmed 3-minute local extremes: list of (index, value) for highs and lows (mid prices)."""
    highs, lows = [], []
    for i in range(3, len(mins) - 3):
        nbh = [mins[j][key_hi] for j in range(i - 3, i + 4) if j != i]
        nbl = [mins[j][key_lo] for j in range(i - 3, i + 4) if j != i]
        if all(mins[i][key_hi] >= x for x in nbh):
            highs.append((i, mins[i][key_hi]))
        if all(mins[i][key_lo] <= x for x in nbl):
            lows.append((i, mins[i][key_lo]))
    return highs, lows


def features(e, pre, L=60):
    ot = S.datetime.fromisoformat(e["opened_at"]).astimezone(timezone.utc).replace(tzinfo=None)
    t0 = ot.replace(second=0, microsecond=0)
    mins = [m for m in pre if t0 - timedelta(minutes=L) <= S.parse(m["t"]) < t0]
    if len(mins) < 20:
        return None
    long_ = e["direction"] == "LONG"
    if long_:
        mm = [{"h": m["h"], "l": m["l"]} for m in mins]
        entry, stop = e["entry_price"], e["stop_loss"]
    else:  # mirror: a short becomes a long on negated prices (highs and lows swap)
        mm = [{"h": -m["l"], "l": -m["h"]} for m in mins]
        entry, stop = -e["entry_price"], -e["stop_loss"]
    risk = entry - stop
    highs, lows = swings(mm)
    below = [(i, v) for i, v in lows if v < entry]
    a1 = bool(below) and max(v for i, v in below) > stop
    a2 = False
    for x in range(len(below)):
        for y in range(x + 1, len(below)):
            if abs(below[x][1] - below[y][1]) <= 0.5 * risk and abs(below[x][0] - below[y][0]) >= 5:
                a2 = True
    b = len(highs) >= 2 and len(lows) >= 2 and highs[-1][1] < highs[-2][1] and lows[-1][1] < lows[-2][1]
    return {"A1": a1, "A2": a2, "B": b}


def mean_t(rs):
    n = len(rs)
    if n < 3:
        return float("nan"), float("nan"), float("nan")
    m = statistics.fmean(rs)
    se = statistics.pstdev(rs) / math.sqrt(n)
    return m, m / se, 0.5 * math.erfc(m / se / math.sqrt(2))


def self_test():
    # a hand-built long: lows 99.0 (min 10) and 99.02 (min 25) form a double bottom, entry 100, stop 98.5
    mins = []
    for i in range(60):
        base = 100.0
        lo = base - 0.5
        if i == 10:
            lo = 99.0
        if i == 25:
            lo = 99.02
        mins.append({"t": f"2026-01-01T00:{i:02d}:00", "h": base + 0.1, "l": lo})
    e = {"direction": "LONG", "entry_price": 100.0, "stop_loss": 98.5, "opened_at": "2026-01-01T01:00:30+00:00"}
    f = features(e, mins)
    assert f and f["A2"] and f["A1"], f
    mirror = {"direction": "SHORT", "entry_price": 100.0, "stop_loss": 101.5, "opened_at": "2026-01-01T01:00:30+00:00"}
    mm = [{"t": m["t"], "h": 200.0 - m["l"], "l": 200.0 - m["h"]} for m in mins]
    g = features(mirror, mm)
    assert g == f, (f, g)
    print("self-test passed: double bottom found for the long and for its mirrored short")


def main():
    self_test()
    pre_all = S.fetch_pre()
    rows = []
    for grp in ("pre", "post"):
        for tid in S.sets[grp]:
            if tid in S.cache and S.cache[tid] and tid in pre_all:
                e = S.journal[tid]
                f = features(e, pre_all[tid])
                if f is not None:
                    rows.append((grp, e, S.cache[tid], f))
    out = {}
    for g, e, cs, f in rows:
        vw = S.orig_tp(e)
        ent = e["entry_price"]
        for k, mult in (("x1", 1), ("x3", 3)):
            out[(e["trade_id"], k)] = S.sim(e, cs, ent + (vw - ent) * mult)[0]
    rnd = random.Random(11)
    print("entries with features:", len(rows), "| share flagged:", {k: f"{sum(1 for r in rows if r[3][k]) / len(rows):.0%}" for k in ("A1", "A2", "B")})
    verdict = {}
    for name, keep_flagged in (("A1", True), ("A2", True), ("B", False)):
        keepset = [(g, e) for g, e, cs, f in rows if f[name] == keep_flagged]
        dropset = [(g, e) for g, e, cs, f in rows if f[name] != keep_flagged]
        print(f"\n=== {name}: keep {'flagged' if keep_flagged else 'UNflagged'} entries -> keeps {len(keepset)} of {len(rows)} ({len(keepset) / len(rows):.0%}) ===")
        for k in ("x1", "x3"):
            for lab, sset in (("ALL", [(g, e) for g, e, cs, f in rows]), ("KEPT", keepset), ("DROPPED", dropset)):
                rs = [out[(e["trade_id"], k)] for g, e in sset if out[(e["trade_id"], k)] is not None]
                m, t, p = mean_t(rs)
                print(f"  {k} {lab:8s} n={len(rs):3d} mean {m:+.3f} t {t:+.2f} p {p:.3f} win {sum(1 for x in rs if x > 0) / max(len(rs), 1):4.0%}")
        ok = True
        for grp in ("pre", "post"):
            ks = [out[(e["trade_id"], "x1")] for g, e in keepset if g == grp and out[(e["trade_id"], "x1")] is not None]
            ds = [out[(e["trade_id"], "x1")] for g, e in dropset if g == grp and out[(e["trade_id"], "x1")] is not None]
            if ks and ds:
                print(f"  {grp} half at x1: kept {statistics.fmean(ks):+.3f} (n {len(ks)}) vs dropped {statistics.fmean(ds):+.3f} (n {len(ds)})")
                ok = ok and statistics.fmean(ks) > statistics.fmean(ds)
            else:
                ok = False
        allr = [out[(e["trade_id"], "x1")] for g, e, cs, f in rows if out[(e["trade_id"], "x1")] is not None]
        nk = len([1 for g, e in keepset if out[(e["trade_id"], "x1")] is not None])
        draws = sorted(statistics.fmean(rnd.sample(allr, nk)) for _ in range(10000))
        kr = [out[(e["trade_id"], "x1")] for g, e in keepset if out[(e["trade_id"], "x1")] is not None]
        mk, tk, pk = mean_t(kr)
        hi = draws[int(0.975 * len(draws))]
        print(f"  placebo (keep {nk} at random): mean {statistics.fmean(draws):+.3f}, 97.5th pct {hi:+.3f}; kept {mk:+.3f}")
        verdict[name] = ok and mk > 0 and pk < 0.05 / 3 and mk > hi
    print("\nVERDICT:", {k: ("SUPPORTED" if v else "NOT SUPPORTED") for k, v in verdict.items()})


if __name__ == "__main__":
    main()
