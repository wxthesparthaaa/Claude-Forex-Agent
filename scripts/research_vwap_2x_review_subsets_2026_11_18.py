# VWAP Scalp 2x-target review, 2026-11-18: post-hoc analysis of real live trades (not pre-registered). Run order: desc, fetch_m1, sim, subsets; each takes the scratch dir holding live_journal.json as its argument.
import json, math, statistics, sys, collections, importlib.util
SP = sys.argv[1]
sys.argv = ["x", SP]
import io, contextlib
spec = importlib.util.spec_from_file_location("vs", SP + "/vwap_sim.py")
vs = importlib.util.module_from_spec(spec)
with contextlib.redirect_stdout(io.StringIO()):
    spec.loader.exec_module(vs)
rows = vs.rows
def val(e, cs, kind):
    if kind == "asrun": return vs.sim(e, cs, e.get('target_multiple') or 1.0)[0]
    if kind == "x2": return vs.sim(e, cs, 2.0)[0]
    if kind == "rr2": return vs.sim(e, cs, None, rr=2.0)[0]
def feat(e, cs):
    ent, sl = e['entry_price'], e['stop_loss']
    m = e.get('target_multiple') or 1.0
    tp_old = ent + (e['take_profit'] - ent) / m
    return {"instrument": e['instrument'], "direction": e['direction'], "group": "metals/oil" if e['instrument'] in ("XAU_USD", "XAG_USD", "WTICO_USD", "BCO_USD") else ("JPY crosses" if e['instrument'].endswith("JPY") else "FX majors"),
            "hour": e['opened_at'][11:13], "intended_rr": abs(tp_old - ent) / abs(ent - sl), "vol_ratio": e.get('vol_ratio'), "entry_z": abs(e['entry_z']) if e.get('entry_z') is not None else None,
            "spread_to_stop": e.get('spread_to_stop')}
data = []
for g, e, cs in rows:
    f = feat(e, cs)
    data.append({"g": g, "f": f, "asrun": val(e, cs, "asrun"), "x2": val(e, cs, "x2"), "rr2": val(e, cs, "rr2")})
data = [x for x in data if x["x2"] is not None and x["rr2"] is not None]
def terciles(key):
    vals = sorted(x["f"][key] for x in data if x["f"][key] is not None)
    return vals[len(vals) // 3], vals[2 * len(vals) // 3]
cuts = {k: terciles(k) for k in ("vol_ratio", "entry_z", "spread_to_stop", "intended_rr")}
def bucket(x, key):
    v = x["f"][key]
    if v is None: return None
    lo, hi = cuts[key]; return "low" if v <= lo else ("mid" if v <= hi else "high")
def hblock(h): h = int(h); return "07-10" if h < 11 else ("11-14" if h < 15 else "15-19")
dims = {"instrument": lambda x: x["f"]["instrument"], "group": lambda x: x["f"]["group"], "direction": lambda x: x["f"]["direction"], "hour block (UTC)": lambda x: hblock(x["f"]["hour"]),
        "vol_ratio tercile": lambda x: bucket(x, "vol_ratio"), "entry_z tercile": lambda x: bucket(x, "entry_z"), "spread_to_stop tercile": lambda x: bucket(x, "spread_to_stop"), "intended RR tercile": lambda x: bucket(x, "intended_rr")}
print("cut points (terciles):", {k: tuple(round(c, 5) for c in v) for k, v in cuts.items()})
print("\nmean R under the TP 2:1-of-stop rule and under the live 2x-VWAP rule, by subset: [pre window | post window | all]; n in brackets")
found = []
for dn, fn in dims.items():
    print(f"\n== {dn}")
    groups = collections.defaultdict(list)
    for x in data:
        k = fn(x)
        if k is not None: groups[k].append(x)
    for k in sorted(groups, key=lambda k: -len(groups[k])):
        out = []
        for kind in ("rr2", "x2"):
            cell = []
            for grp in ("pre", "post", "all"):
                rs = [x[kind] for x in groups[k] if grp == "all" or x["g"] == grp]
                cell.append((statistics.fmean(rs) if rs else float('nan'), len(rs), statistics.pstdev(rs) / math.sqrt(len(rs)) if len(rs) > 1 else float('nan')))
            out.append(cell)
        n = out[0][2][1]
        if n < 12: continue
        fmt = lambda c: f"{c[0][0]:+.2f}|{c[1][0]:+.2f}|{c[2][0]:+.2f} ({c[0][1]},{c[1][1]},{c[2][1]})"
        print(f"   {k:12s} 2:1 {fmt(out[0]):44s} 2xVWAP {fmt(out[1])}")
        for kind, cell in zip(("2:1", "2xVWAP"), out):
            if cell[0][0] > 0 and cell[1][0] > 0 and cell[0][1] >= 12 and cell[1][1] >= 12:
                found.append((dn, k, kind, cell))
print("\nSubsets positive in BOTH windows (n >= 12 each), candidates only (about %d subset cells x 2 rules were looked at, so some positives are expected by chance):" % sum(1 for _ in dims))
for dn, k, kind, cell in found:
    print(f"   {dn}: {k} [{kind}]  pre {cell[0][0]:+.2f} (n={cell[0][1]}), post {cell[1][0]:+.2f} (n={cell[1][1]}), all {cell[2][0]:+.3f} (se {cell[2][2]:.3f}, n={cell[2][1]})")
