"""
Daily research routine (2026-11-12): Japan MOF WEEKLY CROSS-BORDER SECURITIES FLOWS as a yen signal. PRE-REGISTERED before any
outcome was computed.

Data (new to the programme): Ministry of Finance "International Transactions in Securities (Weekly; based on reports from
designated major investors)", https://www.mof.go.jp/policy/international_policy/reference/itn_transactions_in_securities/week.csv
(Shift-JIS, 100 million yen, one row per Sunday-Saturday week since 2005-01-02). Published on the Thursday after the week at
08:50 Tokyo (23:50 UTC Wednesday). Columns used: residents' NET purchases of foreign equity + long-term debt ("Subtotal" of
portfolio investment assets, col 7) and non-residents' NET purchases of Japanese equity + long-term debt (col 18). Short-term
debt is excluded in advance (dominated by foreign official T-bill holdings, no FX-flow meaning).

Mechanism, stated before the test: net portfolio OUTFLOW (Japanese investors buying foreign securities minus foreigners buying
Japanese securities) has to be converted out of yen unless hedged. Japanese institutional allocation flows (lifers, pensions,
toshin) come in multi-week programmes, so a big outflow week tends to be followed by more of the same, and the size is only
public on Thursday morning -> the yen should keep weakening against the other majors during the following week (flow
persistence + delayed public information; the yen has the deepest weekly flow data of any currency).
Known caveats, stated in advance: part of the flow is hedged (no spot FX), and when a Thursday is a Japanese holiday the release
moves (assumed rare; checked by the pre-registered Friday-entry robustness).

Signal for the Thursday T after Saturday S (week end): flow = col7 - col18; z = (flow - mean) / stdev over the previous 52 weeks
(excludes the current week). Every week trades (no threshold): z > 0 (outflow) -> yen weaker -> LONG USD_JPY, EUR_JPY, GBP_JPY,
AUD_JPY, CAD_JPY, NZD_JPY, CHF_JPY; z < 0 -> SHORT all seven. Entry at the open of the 01:00 UTC H1 bar on Thursday T (70 min
after publication, avoids the 21-00 UTC rollover spreads). Ask for long, bid for short. If that bar is missing, skip the week.
Variants (K = 2, alpha 0.025):
  W1 hold one week: exit at the open of the 01:00 UTC bar on the next Thursday (or first later bar), financing 2%/yr pro rata.
  W2 Tokyo-session reaction: exit at the open of the 07:00 UTC bar on Thursday T.
Pre-registered extra robustness for W1: enter Friday 01:00 UTC instead (publication-date doubt).
R = net P&L / (stdev of the previous 400 H1 mid log returns x sqrt(bars held) x entry). Inference clustered by Thursday (all
seven crosses share one decision).
Periods: discovery 2006-01-01..2012-12-31, holdout 2013-01-01..2023-12-31, fresh 2024-01-01..2026-11-10.
Checks (shared evaluator, research_bond_lead_2026_10_24.evaluate): holdout n >= 300, mean > 0, p < alpha, halves, fresh,
+20% spread, entry one H1 bar later, without the best cross, years, >= 5 trades a week.
Expected trades per week: 7 (one decision a week, seven crosses).
Self-test: random-walk prices with random flows must score ~0; a peeking rule (direction from the exit price) clearly positive.
Usage: python3 scripts/research_mof_weekly_flows_2026_11_12.py H1_DIR MOF_CSV
"""
import csv
import importlib.util
import json
import math
import os
import random
import re
import statistics
import sys
from datetime import date, datetime, timedelta, timezone

ROOT = os.path.join(os.path.dirname(__file__), "..")
CROSSES = ["USD_JPY", "EUR_JPY", "GBP_JPY", "AUD_JPY", "CAD_JPY", "NZD_JPY", "CHF_JPY"]
Z_WEEKS, VOL_BARS, ENTRY_H = 52, 400, 1
VARIANTS = {"W1 follow MOF net portfolio outflow, Thursday 01:00 UTC to next Thursday 01:00 UTC": "week",
            "W2 follow MOF net portfolio outflow, Thursday 01:00 to 07:00 UTC": 7}


def _bl():
    spec = importlib.util.spec_from_file_location("bl", os.path.join(ROOT, "scripts", "research_bond_lead_2026_10_24.py"))
    bl = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bl)
    bl.K_RUN, bl.ALPHA = 2, 0.05 / 2
    bl.HOLDOUT_START, bl.FRESH_START = "2013-01-01", "2024-01-01"
    return bl


def _num(s):
    s = s.strip().replace(",", "")
    return float(s) if s not in ("", "-") else None


def load_flows(path):
    """{Saturday week-end date: residents' net foreign purchases (eq+LT) minus non-residents' net Japan purchases (eq+LT)}."""
    raw = open(path, "rb").read().decode("shift_jis", errors="replace")
    out = {}
    for r in csv.reader(raw.splitlines()):
        if not r:
            continue
        m = re.match(r"\s*(\d{4})\D+(\d{1,2})\D+(\d{1,2})\s*\D+?(\d{1,2})\D+(\d{1,2})\s*$", r[0].translate(
            str.maketrans("０１２３４５６７８９", "0123456789")))
        if not m or len(r) < 23:
            continue
        y, m1, d1, m2, d2 = map(int, m.groups())
        start = date(y, m1, d1)
        end = date(y + (1 if m2 < m1 else 0), m2, d2)
        a, l = _num(r[7]), _num(r[18])
        if a is None or l is None or (end - start).days != 6:
            continue
        out[end] = a - l
    return dict(sorted(out.items()))


def signals(flows):
    """{Thursday after week-end Saturday S: z of the flow}, the baseline using only the previous 52 weeks."""
    ws = sorted(flows)
    out = {}
    for k, s in enumerate(ws):
        hist = [flows[w] for w in ws[max(0, k - Z_WEEKS):k]]
        if len(hist) == Z_WEEKS:
            sd = statistics.pstdev(hist)
            if sd > 0:
                out[s + timedelta(days=5)] = (flows[s] - statistics.mean(hist)) / sd
    return out


def cross_trades(bl, inst, cs, sig, mode, delay=0, stress=False, fin=True, peek=False, entry_day=0):
    t = [datetime.fromisoformat(c["time"][:19]).replace(tzinfo=timezone.utc) for c in cs]
    idx = {k: i for i, k in enumerate(t)}
    cl = [float(c["mid"]["c"]) for c in cs]
    lr = [0.0] + [math.log(cl[i] / cl[i - 1]) for i in range(1, len(cl))]
    out = []
    for th, z in sig.items():
        ed = th + timedelta(days=entry_day)
        ie = idx.get(datetime(ed.year, ed.month, ed.day, ENTRY_H, tzinfo=timezone.utc))
        if ie is None or ie < VOL_BARS + 1:
            continue
        if mode == "week":
            nt = th + timedelta(days=7)
            tx = datetime(nt.year, nt.month, nt.day, ENTRY_H, tzinfo=timezone.utc)
            ix = next((j for j in range(ie + 1, min(len(t), ie + 200)) if t[j] >= tx), None)
        else:
            ix = idx.get(datetime(ed.year, ed.month, ed.day, mode, tzinfo=timezone.utc))
        if ix is None or ie + delay >= ix:
            continue
        sd = statistics.pstdev(lr[ie - VOL_BARS:ie])
        if sd <= 0:
            continue
        long_ = (cl[ix] > cl[ie]) if peek else z > 0
        e, x = cs[ie + delay], cs[ix]
        if stress:
            e, x = bl.stressed(e), bl.stressed(x)
        entry = float(e["ask"]["o"]) if long_ else float(e["bid"]["o"])
        exit_ = float(x["bid"]["o"]) if long_ else float(x["ask"]["o"])
        sigma = sd * math.sqrt(ix - ie - delay) * entry
        hours = (t[ix] - t[ie + delay]).total_seconds() / 3600
        r = ((exit_ - entry) if long_ else (entry - exit_)) / sigma - (bl.FIN if fin else 0) * entry * hours / 8760 / sigma
        out.append({"time": str(th), "r": r, "inst": inst})
    return out


def selftest(bl):
    rnd = random.Random(12)
    p, cs, t0 = 110.0, [], datetime(2008, 1, 1, tzinfo=timezone.utc)
    for k in range(24 * 7 * 520):
        o = p * math.exp(rnd.gauss(0, 0.0001))
        p = o * math.exp(rnd.gauss(0, 0.001))
        q = {"o": str(o), "h": str(max(o, p)), "l": str(min(o, p)), "c": str(p)}
        cs.append({"time": (t0 + timedelta(hours=k)).isoformat(), "mid": q, "bid": q, "ask": q})
    fl, s = {}, date(2008, 1, 5)
    for _ in range(520):
        fl[s] = rnd.gauss(0, 5000)
        s += timedelta(days=7)
    sig = signals(fl)
    for mode in ("week", 7):
        rs = [r["r"] for r in cross_trades(bl, "USD_JPY", cs, sig, mode, fin=False)]
        se = statistics.stdev(rs) / math.sqrt(len(rs))
        assert len(rs) > 300 and abs(statistics.mean(rs)) < 3.5 * se, f"look-ahead suspected {statistics.mean(rs):+.4f}"
        leak = [r["r"] for r in cross_trades(bl, "USD_JPY", cs, sig, mode, fin=False, peek=True)]
        assert statistics.mean(leak) > 0.3, f"peeking rule not caught {statistics.mean(leak):+.3f}"
        print(f"Self-test ({mode}): random walk {statistics.mean(rs):+.3f} R (se {se:.3f}, n={len(rs)}); peek {statistics.mean(leak):+.2f} R")


def main(h1_dir, mof_csv):
    bl = _bl()
    selftest(bl)
    flows = load_flows(mof_csv)
    sig = signals(flows)
    print("weeks:", len(flows), min(flows), max(flows), "signals:", len(sig), "share z>0:",
          round(statistics.mean(z > 0 for z in sig.values()), 2))
    data = {i: [c for c in json.load(open(os.path.join(h1_dir, f"{i}_H1_MBA.json"))) if c.get("complete", True)] for i in CROSSES}
    mids = {i: bl.mid_only(cs) for i, cs in data.items()}
    for name, mode in VARIANTS.items():
        def run(src=data, **kw):
            return [r for i in CROSSES for r in cross_trades(bl, i, src[i], sig, mode, **kw)]
        bl.evaluate(name, run(), run(stress=True), run(delay=1), run(src=mids, fin=False))
        if mode == "week":
            fri = run(entry_day=1)
            for k in ("discovery", "holdout", "fresh"):
                rs = [r["r"] for r in fri if bl._period(r["time"]) == k]
                print(f"  Friday-entry robustness {k}: n={len(rs)} mean R {statistics.mean(rs) if rs else float('nan'):+.4f}")


if __name__ == "__main__":
    a = sys.argv[1:]
    main(a[0] if a else "/tmp/research/h1", a[1] if len(a) > 1 else "/tmp/research/mof_week.csv")
