# VWAP Scalp 2x-target review, 2026-11-18: post-hoc analysis of real live trades (not pre-registered). Run order: desc, fetch_m1, sim, subsets; each takes the scratch dir holding live_journal.json as its argument.
import json, math, statistics, sys, collections
from datetime import datetime, timedelta, timezone
SP = sys.argv[1]
d = {e['trade_id']: e for e in json.load(open(SP + "/live_journal.json"))}
sets = json.load(open(SP + "/vwap_sets.json"))
cache = json.load(open(SP + "/vwap_m1_cache.json"))
HOLD = 30

def sim(e, cs, mult, sl_first=True, rr=None):
    """R (in units of the stop distance) of the trade's actual entry under TP = entry + (orig_tp - entry) * mult (mult None = no TP)."""
    ent, sl = e['entry_price'], e['stop_loss']
    m_act = e.get('target_multiple') or 1.0
    orig_tp = ent + (e['take_profit'] - ent) / m_act
    long_ = e['direction'] == 'LONG'
    risk = abs(ent - sl)
    long_dir = 1 if e['direction'] == 'LONG' else -1
    if rr is not None:
        tp = ent + long_dir * rr * risk      # take-profit a fixed multiple of the STOP distance (2:1 etc.), whatever VWAP is
    else:
        tp = None if mult is None else ent + (orig_tp - ent) * mult
    ot = datetime.fromisoformat(e['opened_at']).astimezone(timezone.utc).replace(tzinfo=None)
    t0 = ot.replace(second=0, microsecond=0)
    end = ot + timedelta(minutes=HOLD)
    last = None
    for c in cs:
        t = datetime.fromisoformat(c['t'])
        if t <= t0 or not c.get('complete'):
            continue            # the entry minute itself is skipped (the order filled somewhere inside it)
        if t >= end:
            break
        last = c
        if long_:
            hi, lo = float(c['bid']['h']), float(c['bid']['l'])
            hit_tp = tp is not None and hi >= tp
            hit_sl = lo <= sl
        else:
            hi, lo = float(c['ask']['h']), float(c['ask']['l'])
            hit_tp = tp is not None and lo <= tp
            hit_sl = hi >= sl
        if hit_sl and (sl_first or not hit_tp):
            return -1.0, "stop"
        if hit_tp:
            return (abs(tp - ent) / risk), "tp"
    if last is None:
        return None, "nodata"
    ex = float(last['bid']['c']) if long_ else float(last['ask']['c'])
    return ((ex - ent) if long_ else (ent - ex)) / risk, "cap"

def actual_R(e):
    ex = e.get('exit_price')
    if ex is None: return None
    return ((ex - e['entry_price']) if e['direction'] == 'LONG' else (e['entry_price'] - ex)) / abs(e['entry_price'] - e['stop_loss'])

rows = []
for grp, ids in (("pre", sets['pre']), ("post", sets['post'])):
    for tid in ids:
        if tid in cache and cache[tid]:
            rows.append((grp, d[tid], cache[tid]))
print("trades with minute data:", len(rows), "of", len(sets['pre']) + len(sets['post']))

# validation: replay the strategy as it actually ran and compare with the realised result
va = []
for grp, e, cs in rows:
    m = e.get('target_multiple') or 1.0
    r, how = sim(e, cs, m)
    ra = actual_R(e)
    if r is not None and ra is not None:
        va.append((r, ra, e['realized_pnl'], how))
agree = sum(1 for r, ra, p, h in va if (r > 0) == (p > 0))
cor = statistics.correlation([x[0] for x in va], [x[1] for x in va]) if len(va) > 5 else float('nan')
print(f"VALIDATION replay as-run vs real: sign agreement {agree}/{len(va)} = {agree/len(va):.0%}; correlation of R {cor:.2f}; mean replay R {statistics.fmean(x[0] for x in va):+.3f} vs mean real R {statistics.fmean(x[1] for x in va):+.3f}")

mults = [0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 2.5, 3.0, 4.0, None]
print("\nTP multiple sweep on the SAME entries (mean R in units of the stop distance; win % ; share ending at TP / stop / 30-min cap):")
for grp in ("pre", "post", "all"):
    print(f"-- {grp} entries")
    for m in mults:
        out = []
        for g, e, cs in rows:
            if grp != "all" and g != grp: continue
            r, how = sim(e, cs, m)
            if r is not None: out.append((r, how))
        rs = [x[0] for x in out]; hw = collections.Counter(x[1] for x in out)
        se = statistics.pstdev(rs) / math.sqrt(len(rs))
        print(f"   TP x{m if m else 'none':>5}: n={len(rs):3d} mean R {statistics.fmean(rs):+.3f} (se {se:.3f}) win {sum(1 for x in rs if x > 0)/len(rs):4.0%}  tp {hw['tp']/len(rs):4.0%} stop {hw['stop']/len(rs):4.0%} cap {hw['cap']/len(rs):4.0%}")
# paired difference 2x vs 1x per window
print("\nPaired difference (TP x2 minus TP x1), per trade, in R:")
for grp in ("pre", "post", "all"):
    dif = []
    for g, e, cs in rows:
        if grp != "all" and g != grp: continue
        a, _ = sim(e, cs, 2.0); b, _ = sim(e, cs, 1.0)
        if a is not None and b is not None: dif.append(a - b)
    se = statistics.pstdev(dif) / math.sqrt(len(dif))
    print(f"   {grp}: n={len(dif)} mean {statistics.fmean(dif):+.3f} R (se {se:.3f}, t {statistics.fmean(dif)/se:+.2f})")
json.dump([[g, e['trade_id']] for g, e, cs in rows], open(SP + "/vwap_rows.json", "w"))

print("\nTAKE-PROFIT AS A FIXED MULTIPLE OF THE STOP DISTANCE (reward:risk), same entries:")
for grp in ("pre", "post", "all"):
    print(f"-- {grp} entries")
    for rrv in (0.5, 1.0, 1.5, 2.0, 3.0):
        out = []
        for g, e, cs in rows:
            if grp != "all" and g != grp: continue
            r, how = sim(e, cs, None, rr=rrv)
            if r is not None: out.append((r, how))
        rs = [x[0] for x in out]; hw = collections.Counter(x[1] for x in out)
        se = statistics.pstdev(rs) / math.sqrt(len(rs))
        print(f"   TP = {rrv}:1 of stop: n={len(rs):3d} mean R {statistics.fmean(rs):+.3f} (se {se:.3f}) win {sum(1 for x in rs if x > 0)/len(rs):4.0%}  tp {hw['tp']/len(rs):4.0%} stop {hw['stop']/len(rs):4.0%} cap {hw['cap']/len(rs):4.0%}")
print("\nPaired: TP 2:1 of stop minus the strategy as actually run (VWAP target x1 before / x2 after), per trade, R:")
for grp in ("pre", "post", "all"):
    dif = []
    for g, e, cs in rows:
        if grp != "all" and g != grp: continue
        a, _ = sim(e, cs, None, rr=2.0); b, _ = sim(e, cs, e.get('target_multiple') or 1.0)
        if a is not None and b is not None: dif.append(a - b)
    se = statistics.pstdev(dif) / math.sqrt(len(dif))
    print(f"   {grp}: n={len(dif)} mean {statistics.fmean(dif):+.3f} R (se {se:.3f}, t {statistics.fmean(dif)/se:+.2f})")
