"""
Research, 2026-11-22. PRE-REGISTERED before any result was seen. Follow-up to research_vwap_swing_tp_2026_11_22.py (a nearer take-profit lost). The user's other reading of the same chart: do not
TAKE the trade when a recent ceiling already sits between the entry and the VWAP target (price had rejected that level, so the move back to VWAP has to break it first).
DATA: the same 362 live VWAP Scalp entries 2026-10-14..2026-11-17 with real one-minute bid/ask after entry and the minutes before entry (cache of the swing script).
RULE F(f): swing level = the NEAREST confirmed 3-minute swing high above the entry (swing low below it for a SHORT) inside the 60 minutes before the entry minute, exactly as in the swing script.
Block the entry if that level exists and (swing - entry) / (VWAP target - entry) < f, i.e. it lies within the first fraction f of the way to VWAP. A level beyond the VWAP target never blocks.
VARIANTS (K = 2, alpha 0.05/2 = 0.025, one-sided): F50 (f = 0.50) and F75 (f = 0.75). Trades that pass are taken exactly as before.
OUTCOME: R in units of the stop distance, TP at the VWAP target (x1), also reported at 2x and 3x the VWAP distance (3x is the live setting). A pre-registered placebo: the same number of trades
removed at random (10,000 draws) shows how much of any gain is just luck.
VERDICT RULE: SUPPORTED only if the KEPT trades have mean R > 0 with one-sided p < 0.025 at TP x1 AND the kept set beats the blocked set in BOTH the pre and post halves AND the kept mean sits above
the 97.5th percentile of the random-removal placebo. Anything less is NOT SUPPORTED. Post-hoc caveat: entries come after 17+ refinements; and the 2026-11-20 trade that inspired this is NOT in the sample (it is after 11-17).
Usage: venv/Scripts/python.exe scripts/research_vwap_swing_filter_2026_11_22.py SCRATCH_DIR
"""
import math
import os
import random
import statistics
import sys

sys.path.insert(0, os.path.dirname(__file__))
import research_vwap_swing_tp_2026_11_22 as S  # noqa: E402  (loads the same journal / minute caches from sys.argv[1])


def frac_to_ceiling(e, pre, L=60):
    lvl = S.swing_level(e, pre, L)
    if lvl is None:
        return None
    vw = S.orig_tp(e)
    den = abs(vw - e["entry_price"])
    if den <= 0:
        return None
    long_ = e["direction"] == "LONG"
    num = (lvl - e["entry_price"]) if long_ else (e["entry_price"] - lvl)
    return num / den


def mean_t(rs):
    n = len(rs)
    if n < 3:
        return float("nan"), float("nan"), float("nan")
    m = statistics.fmean(rs)
    se = statistics.pstdev(rs) / math.sqrt(n)
    t = m / se
    return m, t, 0.5 * math.erfc(t / math.sqrt(2))


def main():
    pre_all = S.fetch_pre()
    rows = []
    for grp in ("pre", "post"):
        for tid in S.sets[grp]:
            if tid in S.cache and S.cache[tid] and tid in pre_all:
                rows.append((grp, S.journal[tid], S.cache[tid], pre_all[tid]))
    outcomes = {}
    for g, e, cs, pre in rows:
        vw = S.orig_tp(e)
        ent = e["entry_price"]
        for k, mult in (("x1", 1), ("x2", 2), ("x3", 3)):
            r, how = S.sim(e, cs, ent + (vw - ent) * mult)
            outcomes[(e["trade_id"], k)] = r
    fr = {e["trade_id"]: frac_to_ceiling(e, pre) for g, e, cs, pre in rows}
    print("entries:", len(rows), "| with a swing level:", sum(1 for v in fr.values() if v is not None), "| fraction-to-ceiling quartiles:",
          [round(x, 2) for x in statistics.quantiles([v for v in fr.values() if v is not None], n=4)])
    rnd = random.Random(7)
    verdict = {}
    for name, f in (("F50", 0.50), ("F75", 0.75)):
        blocked = {tid for tid, v in fr.items() if v is not None and v < f}
        print(f"\n=== {name}: block when the ceiling lies within the first {int(f * 100)}% of the way to VWAP -> blocks {len(blocked)} of {len(rows)} ({len(blocked) / len(rows):.0%}); keeps {len(rows) - len(blocked)} ===")
        print("TP rule | set | n | mean R | t | one-sided p | win %")
        for k in ("x1", "x2", "x3"):
            for lab, sel in (("ALL", lambda t, g: True), ("KEPT", lambda t, g: t not in blocked), ("BLOCKED", lambda t, g: t in blocked)):
                rs = [outcomes[(e["trade_id"], k)] for g, e, cs, pre in rows if sel(e["trade_id"], g) and outcomes[(e["trade_id"], k)] is not None]
                m, t, p = mean_t(rs)
                print(f"  {k} {lab:8s} n={len(rs):3d} mean {m:+.3f} t {t:+.2f} p {p:.3f} win {sum(1 for x in rs if x > 0) / max(len(rs), 1):4.0%}")
        ok = True
        for grp in ("pre", "post"):
            ks = [outcomes[(e["trade_id"], "x1")] for g, e, cs, pre in rows if g == grp and e["trade_id"] not in blocked and outcomes[(e["trade_id"], "x1")] is not None]
            bs = [outcomes[(e["trade_id"], "x1")] for g, e, cs, pre in rows if g == grp and e["trade_id"] in blocked and outcomes[(e["trade_id"], "x1")] is not None]
            print(f"  {grp} half at x1: kept {statistics.fmean(ks):+.3f} (n {len(ks)}) vs blocked {statistics.fmean(bs):+.3f} (n {len(bs)})")
            ok = ok and statistics.fmean(ks) > statistics.fmean(bs)
        allr = [outcomes[(e["trade_id"], "x1")] for g, e, cs, pre in rows if outcomes[(e["trade_id"], "x1")] is not None]
        nb = len(blocked)
        draws = []
        for _ in range(10000):
            drop = set(rnd.sample(range(len(allr)), nb))
            draws.append(statistics.fmean([x for i, x in enumerate(allr) if i not in drop]))
        draws.sort()
        kept = [outcomes[(e["trade_id"], "x1")] for g, e, cs, pre in rows if e["trade_id"] not in blocked and outcomes[(e["trade_id"], "x1")] is not None]
        mk, tk, pk = mean_t(kept)
        hi = draws[int(0.975 * len(draws))]
        print(f"  placebo (remove {nb} at random): mean R of the rest averages {statistics.fmean(draws):+.3f}, 97.5th percentile {hi:+.3f}; kept set {mk:+.3f}")
        verdict[name] = ok and mk > 0 and pk < 0.025 and mk > hi
    print("\nVERDICT:", {k: ("SUPPORTED" if v else "NOT SUPPORTED") for k, v in verdict.items()})
    # trades per week impact
    days = (max(S.parse(e["opened_at"]) for g, e, cs, pre in rows) - min(S.parse(e["opened_at"]) for g, e, cs, pre in rows)).days
    print(f"entries per week now: {len(rows) / (days / 7):.1f}")


if __name__ == "__main__":
    main()
