"""
Research, 2026-10-25. PRE-REGISTERED before any result was seen. A ONE-TIME, EXHAUSTIVE SCAN with strict multiple-testing control, to answer the standing question once instead of one hypothesis at a time:
does ANY free outside-OANDA daily series, under simple transformations, predict the next week's return of any of the 28 instruments (10 indices, 12 FX pairs, 6 commodities) well enough to survive?
SERIES BANK (about 70): 54 US-listed ETFs (9 sector SPDRs plus XLRE, country ETFs EEM EFA EWJ EWG EWU FXI EWZ INDA EWY EWT, commodity ETFs USO UNG SLV DBA CPER DBC GDX GDXJ, bond ETFs TIP EMB MUB BND SHY IEF
TLT HYG LQD JNK, currency ETFs UUP FXE FXY FXA FXB FXC, equity ETFs SPY QQQ IWM DIA XHB KRE SMH XBI ITB VNQ), GLD, and the volatility/rates gauges already collected (VIX, VIX3M, VIX9D, VVIX, SKEW, VXN, MOVE, OVX,
GVZ, the dollar index, 10-year / 3-month / 30-year yields, S&P 500). TRANSFORMS (4 per series): 1-day log change, 5-day log change, 20-day log change, and the level; each turned into a causal z-score against
the previous 250 observations (today excluded; forward-filled over holidays). For yields the change is a difference. That is about 280 signals x 28 instruments = about 7,800 cells.
TARGET: gross mid-price R of the 5-day hold entered at the open of the first daily candle after the signal date (the VT1 engine's entry/exit), in units of 60-day daily volatility x sqrt(5).
STATISTIC per cell: the correlation between the signal z and the target R over the DISCOVERY period (2012-03-01..2017-12-31); t-statistic = r x sqrt((n-2)/(1-r^2)) DIVIDED BY sqrt(5) to deflate for the
overlap of 5-day returns on consecutive days (conservative). SELECTION: |t| >= 4.4 (two-sided Bonferroni for 7,800 cells at 0.05 is about 4.6; 4.4 is used so a near miss is visible; the expected number
of false selections under the null is about 0.4).
JUDGEMENT of selected cells (no tuning after seeing the holdout): trade sign(correlation) x sign(z) whenever |z| >= 1 on the cell's instrument, 5-day hold, daily engine with real bid/ask and financing,
HOLDOUT 2018-01-01..2025-09-30 and FRESH 2025-10-01..; a cell is a FINDING only if its holdout mean net R > 0 with one-sided date-clustered p < 0.05 divided by the number selected AND the +20% spread mean > 0.
ALSO REPORTED (no claim): (a) how many cells exceed |t| 3, 3.5, 4 in discovery against the number expected by chance; (b) the replication rate -- among cells with discovery |t| >= 3, the share whose holdout
correlation has the SAME SIGN and the mean of (sign-adjusted) holdout correlation (zero under the null, positive if the discovery signals carry any real information); (c) the 15 largest discovery |t| cells with their holdout t.
Self-test: on random series and random walks nothing is selected and the replication correlation is ~0; a planted real predictor (a series equal to the future 5-day return plus noise) is selected with the right sign.
"""
import glob
import json
import math
import os
import random
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timedelta

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
import research_bond_lead_2026_10_24 as bl
import research_ml_macro_2026_10_25 as mm
import research_vol_surface_2026_10_25 as vs
import research_vol_surface_replication_2026_10_25 as rep

DISC = ("2012-03-01", "2018-01-01")
HOLD_P = ("2018-01-01", "2025-10-01")
FRESH_P = ("2025-10-01", "2027-01-01")
T_SELECT = 4.4
SPAN = 250


def rolling_z(a):
    """causal z against the previous SPAN observations (today excluded); NaN where < 120 prior."""
    n = len(a)
    out = np.full(n, np.nan)
    c1 = np.concatenate([[0.0], np.cumsum(np.nan_to_num(a))])
    c2 = np.concatenate([[0.0], np.cumsum(np.nan_to_num(a) ** 2)])
    for i in range(120, n):
        lo = max(0, i - SPAN)
        k = i - lo
        mu = (c1[i] - c1[lo]) / k
        var = (c2[i] - c2[lo]) / k - mu * mu
        if var > 1e-18 and not np.isnan(a[i]):
            out[i] = (a[i] - mu) / math.sqrt(var)
    return out


def transforms(vals, is_diff):
    """vals: array on the common trading calendar (forward-filled). Returns {name: raw transform array}."""
    n = len(vals)
    out = {"level": vals.copy()}
    for k, nm in ((1, "chg1"), (5, "chg5"), (20, "chg20")):
        t = np.full(n, np.nan)
        if is_diff:
            t[k:] = vals[k:] - vals[:-k]
        else:
            with np.errstate(divide="ignore", invalid="ignore"):
                t[k:] = np.log(vals[k:] / vals[:-k])
        out[nm] = t
    return out


def target_matrix(dates, data):
    """{inst: np.array of gross 5-day R per signal date (NaN where unavailable)}."""
    res = {}
    for inst, cs in data.items():
        labels = [rep.label(c) for c in cs]
        op = np.array([float(c["mid"]["o"]) for c in cs])
        cl = [float(c["mid"]["c"]) for c in cs]
        lr = np.array([0.0] + [math.log(cl[i] / cl[i - 1]) for i in range(1, len(cl))])
        arr = np.full(len(dates), np.nan)
        j = 0
        for n, d in enumerate(dates):
            while j < len(labels) and labels[j] <= d:
                j += 1
            ei = j
            if ei < 62 or ei + 5 >= len(cs):
                continue
            if labels[ei] > (datetime.strptime(d, "%Y-%m-%d") + timedelta(days=5)).strftime("%Y-%m-%d"):
                continue
            sd = lr[ei - 60:ei].std()
            if sd > 0:
                arr[n] = (op[ei + 5] - op[ei]) / op[ei] / (sd * math.sqrt(5))
        res[inst] = arr
    return res


def corr_stats(x, y, mask):
    m = mask & ~np.isnan(x) & ~np.isnan(y)
    n = int(m.sum())
    if n < 150:
        return None
    xs, ys = x[m], y[m]
    if xs.std() == 0 or ys.std() == 0:
        return None
    r = float(np.corrcoef(xs, ys)[0, 1])
    t = r * math.sqrt((n - 2) / max(1e-12, 1 - r * r)) / math.sqrt(5)
    return r, t, n


def build_cells(dates, series_arrays, targets):
    di = np.array(dates)
    masks = {"disc": (di >= DISC[0]) & (di < DISC[1]), "hold": (di >= HOLD_P[0]) & (di < HOLD_P[1])}
    cells = []
    for sname, arr in series_arrays.items():
        for tname, z in arr.items():
            for inst, y in targets.items():
                a = corr_stats(z, y, masks["disc"])
                b = corr_stats(z, y, masks["hold"])
                if a and b:
                    cells.append((sname, tname, inst, a[0], a[1], a[2], b[0], b[1]))
    return cells


def selftest():
    rnd = np.random.RandomState(3)
    T = 3500
    dates = [(datetime(2011, 3, 1) + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(T)]
    y = rnd.randn(T)
    series = {f"noise{k}": {"level": rolling_z(rnd.randn(T)), "chg1": rolling_z(rnd.randn(T))} for k in range(40)}
    cells = build_cells(dates, series, {"I": y})
    sel = [c for c in cells if abs(c[4]) >= T_SELECT]
    assert not sel, f"null scan selected {sel[:2]}"
    series["planted"] = {"level": rolling_z(y + 0.5 * rnd.randn(T))}
    cells = build_cells(dates, series, {"I": y})
    sel = [c for c in cells if abs(c[4]) >= T_SELECT]
    assert any(c[0] == "planted" and c[3] > 0 for c in sel), "planted predictor not found"
    rep_corr = statistics.fmean([c[6] * (1 if c[3] > 0 else -1) for c in cells if c[0] != "planted" and abs(c[4]) >= 1.5] or [0.0])
    print(f"Self-test passed: 80 random cells select nothing; a planted predictor is selected (t={[c[4] for c in sel if c[0] == 'planted'][0]:.1f}); "
          f"sign-adjusted holdout correlation of weak random cells {rep_corr:+.3f} (~0).")


def main(d1_dirs, ext_dir, cboe_dir):
    selftest()
    yh = lambda path: mm.load_yahoo(path)
    raw = {}
    for p in sorted(glob.glob(os.path.join(ext_dir, "y_*.json"))):
        name = os.path.basename(p)[2:-5]
        if name in ("TNX", "IRX", "TYX"):
            raw[name] = (yh(p), True)
        elif name not in ("MOVE", "OVX", "GVZ", "VXN", "VIX9D") and name.startswith("^") is False:
            raw[name] = (yh(p), False)
    for nm in ("MOVE", "OVX", "GVZ", "VXN", "VIX9D"):
        raw[nm] = (yh(os.path.join(ext_dir, f"y_{nm}.json")), False)
    for nm, f in (("VIX", "cboe_VIX.csv"), ("VIX3M", "cboe_VIX3M.csv"), ("VVIX", "cboe_VVIX.csv"), ("SKEW", "cboe_SKEW.csv")):
        raw[nm] = (vs.load_cboe(os.path.join(cboe_dir, f)), False)
    gspc = raw["GSPC"][0]
    dates = [d for d in sorted(gspc) if d >= "2011-03-01"]
    full = sorted(gspc)
    idx0 = full.index(dates[0])
    cal = full[max(0, idx0 - 300):]
    series_arrays = {}
    for name, (s, is_diff) in raw.items():
        vals, last = [], np.nan
        for d in cal:
            if d in s and s[d] is not None and s[d] > (-1e18):
                last = s[d]
            vals.append(last)
        v = np.array(vals, dtype=float)
        if is_diff is False and np.nanmin(v) <= 0:
            continue  # log changes need positive series
        zs = {}
        for tname, t in transforms(v, is_diff).items():
            zs[tname] = rolling_z(t)[-len(dates):]
        series_arrays[name] = zs
    print(f"{len(series_arrays)} series x 4 transforms")
    data = {}
    from research_ml_macro_2026_10_25 import INDICES, FX, COMMODITIES
    for inst in INDICES + FX + COMMODITIES:
        for d in d1_dirs:
            path = os.path.join(d, f"{inst}_D_MBA.json")
            if os.path.exists(path):
                data[inst] = [c for c in json.load(open(path)) if c.get("complete", True)]
                break
    targets = target_matrix(dates, data)
    cells = build_cells(dates, series_arrays, targets)
    print(f"{len(cells)} cells")
    # (a) excess over chance
    from math import erf, sqrt
    two_sided = lambda t: 1 - erf(t / sqrt(2))
    print("\n(a) cells with |discovery t| above a level, against the number expected by chance:")
    for t in (3.0, 3.5, 4.0, 4.4):
        obs = sum(abs(c[4]) >= t for c in cells)
        print(f"   |t| >= {t}: observed {obs}, expected by chance {len(cells) * two_sided(t):.1f}")
    # (b) replication of discovery signals in the holdout
    for lo in (2.5, 3.0, 3.5):
        pick = [c for c in cells if abs(c[4]) >= lo]
        if pick:
            same = sum((c[3] > 0) == (c[6] > 0) for c in pick)
            adj = [c[6] * (1 if c[3] > 0 else -1) for c in pick]
            print(f"(b) discovery |t| >= {lo}: {len(pick)} cells, holdout same sign in {same} ({same / len(pick):.0%}), mean sign-adjusted holdout correlation {statistics.fmean(adj):+.4f} "
                  f"(se {statistics.pstdev(adj) / math.sqrt(len(adj)):.4f})")
    # (c) top 15
    print("\n(c) 15 largest discovery |t| cells: series/transform/instrument, discovery r (t), holdout r (t):")
    for c in sorted(cells, key=lambda c: -abs(c[4]))[:15]:
        print(f"   {c[0]:6s} {c[1]:6s} {c[2]:10s} disc r {c[3]:+.3f} (t {c[4]:+.2f}, n={c[5]})  holdout r {c[6]:+.3f} (t {c[7]:+.2f})")
    # judgement of selected cells
    sel = [c for c in cells if abs(c[4]) >= T_SELECT]
    print(f"\nSELECTED at |t| >= {T_SELECT}: {len(sel)} cells (expected by chance about {len(cells) * two_sided(T_SELECT):.1f})")
    alpha = 0.05 / max(1, len(sel))
    all_rows = []
    for c in sel:
        z = series_arrays[c[0]][c[1]]
        sgn = 1 if c[3] > 0 else -1
        decs = [(dates[i], sgn * (1 if z[i] > 0 else -1)) for i in range(len(dates)) if not np.isnan(z[i]) and abs(z[i]) >= 1.0 and dates[i] >= HOLD_P[0]]
        long_rate = 0.065 if c[2] in INDICES else 0.02
        base = [dict(r, inst=c[2]) for r in rep.trades(data[c[2]], decs, long_rate=long_rate)]
        stress = rep.trades(data[c[2]], decs, long_rate=long_rate, stress=True)
        h = rep.per(base, *HOLD_P)
        f = rep.per(base, *FRESH_P)
        p = rep.clustered_p(h)
        print(f"   {c[0]} {c[1]} -> {c[2]}: disc t {c[4]:+.2f}; holdout net {rep.m(h):+.4f} (n={len(h)}, p={p:.4f}, +20% {rep.m(rep.per(stress, *HOLD_P)):+.4f}); fresh {rep.m(f):+.4f} (n={len(f)}) "
              f"-> {'FINDING' if rep.m(h) > 0 and p < alpha and rep.m(rep.per(stress, *HOLD_P)) > 0 else 'not a finding'}")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--selftest":
        selftest()
    else:
        main(sys.argv[1].split(";"), sys.argv[2], sys.argv[3])
