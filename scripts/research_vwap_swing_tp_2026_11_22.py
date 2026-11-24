"""
Research, 2026-11-22. PRE-REGISTERED before any result was seen. User idea (looking at a failed USD_JPY long on 2026-11-20): the VWAP target sits above a ceiling price had already
rejected (highs at 18:18, 18:24, 18:54), so take profit at the nearest recent swing high instead of at VWAP.
DATA: the same 372 real VWAP Scalp entries 2026-10-14..2026-11-17 (186 'pre' = TP at VWAP x1/x2 era, 186 'post') and their real one-minute bid/ask after entry, from the 2026-11-18 review
(scratch dir: live_journal.json, vwap_sets.json, vwap_m1_cache.json), PLUS the minutes BEFORE each entry (fetched here), so the swing is known at entry time (causal).
RULE S(L): swing high = a minute whose MID high is >= every mid high in the 3 minutes either side, inside the L minutes before the entry minute (the right-hand 3 minutes must also be before
the entry minute, so it is confirmed). For a LONG, the TP is the NEAREST swing high above the entry price, but never beyond the original VWAP target; if there is none, the TP stays at the VWAP
target. A SHORT mirrors it with swing lows. Everything else (entry, stop, 30-minute cap, stop-first on a tie, bid/ask fills) is the 2026-11-18 replay engine unchanged.
VARIANTS (K = 2, alpha 0.05/2 = 0.025, one-sided): S60 (L = 60) and S30 (L = 30).
BASELINES on the same entries: TP at the VWAP target (x1) and at 2x the VWAP distance.
VERDICT RULE: a variant is SUPPORTED only if (a) its pooled mean R (in units of the stop distance) is > 0 with a one-sided t-test p < 0.025, AND (b) its paired gain over the x1 baseline is > 0 in
BOTH the 'pre' and 'post' halves. Anything less is NOT SUPPORTED. Post-hoc caveat: the entries themselves came after 17+ refinements of the live rules.
Usage: venv/Scripts/python.exe scripts/research_vwap_swing_tp_2026_11_22.py SCRATCH_DIR
"""
import collections
import json
import math
import os
import statistics
import sys
import time
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
SP = sys.argv[1]
HOLD = 30
journal = {e["trade_id"]: e for e in json.load(open(os.path.join(SP, "live_journal.json")))}
sets = json.load(open(os.path.join(SP, "vwap_sets.json")))
cache = json.load(open(os.path.join(SP, "vwap_m1_cache.json")))
PRE_PATH = os.path.join(SP, "vwap_premins_cache.json")


def parse(t):
    return datetime.fromisoformat(t[:19])


def fetch_pre():
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))
    from oanda_client import OandaClient
    client = OandaClient()
    out = json.load(open(PRE_PATH)) if os.path.exists(PRE_PATH) else {}
    ids = sets["pre"] + sets["post"]
    for k, tid in enumerate(ids):
        if tid in out:
            continue
        e = journal[tid]
        ot = datetime.fromisoformat(e["opened_at"]).astimezone(timezone.utc).replace(tzinfo=None)
        t0 = ot.replace(second=0, microsecond=0)
        try:
            cs = client.get_candles(e["instrument"], "M1", from_time=(t0 - timedelta(minutes=75)).strftime("%Y-%m-%dT%H:%M:%SZ"), to_time=t0.strftime("%Y-%m-%dT%H:%M:%SZ"))
            out[tid] = [{"t": c["time"][:19], "h": float(c["mid"]["h"]), "l": float(c["mid"]["l"])} for c in cs if c.get("complete", True)]
        except Exception as ex:  # noqa: BLE001
            print("fetch failed", tid, repr(ex)[:80], flush=True)
            time.sleep(2)
        if k % 50 == 0:
            print("fetched", k, "of", len(ids), flush=True)
            json.dump(out, open(PRE_PATH, "w"))
    json.dump(out, open(PRE_PATH, "w"))
    return out


def swing_level(e, pre, L):
    """Nearest confirmed swing high above (LONG) / swing low below (SHORT) the entry price inside the last L minutes, or None."""
    ot = datetime.fromisoformat(e["opened_at"]).astimezone(timezone.utc).replace(tzinfo=None)
    t0 = ot.replace(second=0, microsecond=0)
    mins = [m for m in pre if parse(m["t"]) < t0 and parse(m["t"]) >= t0 - timedelta(minutes=L)]
    long_ = e["direction"] == "LONG"
    ent = e["entry_price"]
    key = "h" if long_ else "l"
    best = None
    for i in range(3, len(mins) - 3):
        v = mins[i][key]
        nb = [mins[j][key] for j in range(i - 3, i + 4) if j != i]
        ok = all(v >= x for x in nb) if long_ else all(v <= x for x in nb)
        if not ok:
            continue
        if long_ and v > ent:
            best = v if best is None else min(best, v)
        if (not long_) and v < ent:
            best = v if best is None else max(best, v)
    return best


def sim(e, cs, tp):
    ent, sl = e["entry_price"], e["stop_loss"]
    long_ = e["direction"] == "LONG"
    risk = abs(ent - sl)
    ot = datetime.fromisoformat(e["opened_at"]).astimezone(timezone.utc).replace(tzinfo=None)
    t0 = ot.replace(second=0, microsecond=0)
    end = ot + timedelta(minutes=HOLD)
    last = None
    for c in cs:
        t = datetime.fromisoformat(c["t"])
        if t <= t0 or not c.get("complete"):
            continue
        if t >= end:
            break
        last = c
        if long_:
            hi, lo = float(c["bid"]["h"]), float(c["bid"]["l"])
            hit_tp, hit_sl = hi >= tp, lo <= sl
        else:
            hi, lo = float(c["ask"]["h"]), float(c["ask"]["l"])
            hit_tp, hit_sl = lo <= tp, hi >= sl
        if hit_sl:
            return -1.0, "stop"
        if hit_tp:
            return abs(tp - ent) / risk, "tp"
    if last is None:
        return None, "nodata"
    ex = float(last["bid"]["c"]) if long_ else float(last["ask"]["c"])
    return ((ex - ent) if long_ else (ent - ex)) / risk, "cap"


def orig_tp(e):
    m = e.get("target_multiple") or 1.0
    return e["entry_price"] + (e["take_profit"] - e["entry_price"]) / m


def tp_for(e, pre, rule):
    vw = orig_tp(e)
    if rule == "x1":
        return vw
    if rule == "x2":
        return e["entry_price"] + (vw - e["entry_price"]) * 2
    L = int(rule[1:])
    s = swing_level(e, pre, L)
    if s is None:
        return vw
    long_ = e["direction"] == "LONG"
    return min(s, vw) if long_ else max(s, vw)


def p_one_sided(rs):
    n = len(rs)
    t = statistics.fmean(rs) / (statistics.pstdev(rs) / math.sqrt(n))
    return t, 0.5 * math.erfc(t / math.sqrt(2))


def main():
    pre_all = fetch_pre()
    # self-test: a TP exactly at the entry price must exit as a tiny win (or stop first) and never create a loss larger than 1R; a stop-first tie is respected
    rows = []
    for grp in ("pre", "post"):
        for tid in sets[grp]:
            if tid in cache and cache[tid] and tid in pre_all:
                rows.append((grp, journal[tid], cache[tid], pre_all[tid]))
    print("entries with both minute sets:", len(rows), "of", len(sets["pre"]) + len(sets["post"]))
    res = {}
    for rule in ("x1", "x2", "S60", "S30"):
        res[rule] = []
        for g, e, cs, pre in rows:
            r, how = sim(e, cs, tp_for(e, pre, rule))
            res[rule].append((g, r, how, e["trade_id"]))
    for rule in ("S60", "S30"):
        used = sum(1 for g, e, cs, pre in rows if swing_level(e, pre, int(rule[1:])) is not None)
        near = [abs(tp_for(e, pre, rule) - e["entry_price"]) / abs(orig_tp(e) - e["entry_price"]) for g, e, cs, pre in rows if abs(orig_tp(e) - e["entry_price"]) > 0]
        print(f"{rule}: a swing level exists for {used}/{len(rows)} entries; median TP distance = {statistics.median(near):.2f} of the VWAP distance")
    print("\nrule | group | n | mean R (stop units) | t | one-sided p | win % | TP / stop / cap share")
    for rule in ("x1", "x2", "S60", "S30"):
        for grp in ("pre", "post", "all"):
            rr = [(r, how) for g, r, how, _ in res[rule] if r is not None and (grp == "all" or g == grp)]
            rs = [x[0] for x in rr]
            t, p = p_one_sided(rs)
            hw = collections.Counter(x[1] for x in rr)
            print(f"{rule:3s} {grp:4s} n={len(rs):3d} mean {statistics.fmean(rs):+.3f}  t {t:+.2f} p {p:.3f}  win {sum(1 for x in rs if x > 0) / len(rs):4.0%}  tp {hw['tp'] / len(rs):4.0%} stop {hw['stop'] / len(rs):4.0%} cap {hw['cap'] / len(rs):4.0%}")
    print("\nPaired gain over TP at VWAP x1 (R per trade):")
    verdict = {}
    for rule in ("S60", "S30"):
        ok = True
        for grp in ("pre", "post", "all"):
            dif = []
            for (g1, r1, _, t1), (g2, r2, _, t2) in zip(res[rule], res["x1"]):
                if r1 is not None and r2 is not None and (grp == "all" or g1 == grp):
                    dif.append(r1 - r2)
            se = statistics.pstdev(dif) / math.sqrt(len(dif))
            print(f"  {rule} {grp}: n={len(dif)} mean {statistics.fmean(dif):+.3f} (se {se:.3f}, t {statistics.fmean(dif) / se:+.2f})")
            if grp in ("pre", "post"):
                ok = ok and statistics.fmean(dif) > 0
        pooled = [r for g, r, h, _ in res[rule] if r is not None]
        t, p = p_one_sided(pooled)
        verdict[rule] = ok and statistics.fmean(pooled) > 0 and p < 0.025
    print("\nVERDICT:", {k: ("SUPPORTED" if v else "NOT SUPPORTED") for k, v in verdict.items()})
    # the USD_JPY example
    ex = [(g, e, cs, pre) for g, e, cs, pre in rows if e["instrument"] == "USD_JPY" and e["opened_at"].startswith("2026-11-20T18:38")]
    if ex:
        g, e, cs, pre = ex[0]
        print("\nThe 2026-11-20 USD_JPY long: swing S60 =", swing_level(e, pre, 60), "VWAP target x1 =", round(orig_tp(e), 3), "-> S60 outcome", sim(e, cs, tp_for(e, pre, "S60")), "| x1 outcome", sim(e, cs, tp_for(e, pre, "x1")))


if __name__ == "__main__":
    main()
