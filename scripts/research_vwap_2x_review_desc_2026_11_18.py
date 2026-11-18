# VWAP Scalp 2x-target review, 2026-11-18: post-hoc analysis of real live trades (not pre-registered). Run order: desc, fetch_m1, sim, subsets; each takes the scratch dir holding live_journal.json as its argument.
import json, statistics, collections, math, sys
from datetime import datetime
SP = sys.argv[1]
d = json.load(open(SP + "/live_journal.json"))
v = [e for e in d if e.get('experiment_tag') == 'VWAP_SCALP' and e.get('realized_pnl') is not None]
v.sort(key=lambda e: e['opened_at'])
post = [e for e in v if e.get('target_multiple') == 2.0]
pre_all = [e for e in v if e.get('target_multiple') is None]
pre = pre_all[-len(post):]
def R(e):  # realized result in units of the risk budget of that trade
    return e['realized_pnl'] / e['risk_amount'] if e.get('risk_amount') else None
def stats(g, label):
    pn = [e['realized_pnl'] for e in g]; w = [x for x in pn if x > 0]; l = [x for x in pn if x <= 0]
    rs = [R(e) for e in g if R(e) is not None]
    se = statistics.pstdev(pn) / math.sqrt(len(pn))
    print(f"{label:46s} n={len(g):4d} win {len(w)/len(g):5.1%} avg win {statistics.fmean(w):+6.2f} avg loss {statistics.fmean(l):+6.2f} net {sum(pn):+8.2f} /trade {statistics.fmean(pn):+6.3f} (se {se:.2f})  mean R(vs risk) {statistics.fmean(rs):+.3f}")
span = lambda g: (g[0]['opened_at'][:10], g[-1]['opened_at'][:10])
print("pre-2x window :", span(pre), "| 2x window:", span(post))
stats(pre, "BEFORE 2x (the same number of trades just prior)")
stats(post, "WITH 2x target")
stats(pre_all[-400:], "before 2x, last 400 trades")
print()
# per day
for label, g in (("pre", pre), ("2x", post)):
    by = collections.defaultdict(list)
    for e in g: by[e['opened_at'][:10]].append(e['realized_pnl'])
    print(label, "trades/day", round(len(g)/len(by),1), "| days", len(by), "| positive days", sum(1 for x in by.values() if sum(x) > 0), "| daily net:", " ".join(f"{sum(x):+.0f}" for k, x in sorted(by.items())))
# chronological thirds of the 2x trades
print()
n = len(post)
for i in range(3):
    stats(post[i*n//3:(i+1)*n//3], f"2x trades, third {i+1}")
# exit type
def exit_type(e):
    ent, sl, tp, ex = e['entry_price'], e['stop_loss'], e['take_profit'], e.get('exit_price')
    if ex is None: return "unknown"
    tol = abs(ent - sl) * 0.05 + 1e-12
    if abs(ex - tp) <= tol: return "take-profit"
    if abs(ex - sl) <= tol: return "stop-loss"
    mins = (datetime.fromisoformat(e['closed_at']) - datetime.fromisoformat(e['opened_at'])).total_seconds() / 60
    return "30-min cap" if mins >= 28 else "other"
print()
for label, g in (("pre", pre), ("2x", post)):
    c = collections.Counter(exit_type(e) for e in g)
    print(label, "exits:", {k: f"{n_} ({n_/len(g):.0%})" for k, n_ in c.most_common()})
    for k in c:
        sub = [e['realized_pnl'] for e in g if exit_type(e) == k]
        print(f"     {k:12s} n={len(sub):3d} mean {statistics.fmean(sub):+.2f} SGD")
# geometry
def rr(e): return abs(e['take_profit'] - e['entry_price']) / abs(e['entry_price'] - e['stop_loss'])
for label, g in (("pre", pre), ("2x", post)):
    r = [rr(e) for e in g]
    print(label, "TP distance / SL distance: median", round(statistics.median(r), 2), "mean", round(statistics.fmean(r), 2), "| share with TP farther than SL:", f"{sum(1 for x in r if x > 1)/len(r):.0%}")
json.dump({"post": [e['trade_id'] for e in post], "pre": [e['trade_id'] for e in pre]}, open(SP + "/vwap_sets.json", "w"))
