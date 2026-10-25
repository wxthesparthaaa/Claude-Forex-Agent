"""
Research, 2026-10-25. PRE-REGISTERED before any result was seen. The US dollar-liquidity channel: when the US Treasury builds up its cash account at the Federal Reserve (the TGA) or money-market funds
park cash in the Fed's overnight reverse-repo facility (RRP), that cash leaves the banking system's reserves; when they run down, cash returns. A widely followed practitioner thesis ("net liquidity")
is that risk assets drift down when this liquidity is drained and up when it is injected. Both series are public, daily and free (US Treasury FiscalData Daily Treasury Statement; New York Fed
reverse-repo operations). The Fed's own balance-sheet changes (the third leg) are NOT available here (FRED is unreachable), so this is a two-legged proxy, stated up front. Never tested here (grepped).
SERIES: TGA closing balance ($ millions; 'Federal Reserve Account' until 2022-04, then 'Treasury General Account (TGA) Closing Balance': the same quantity under the DTS's renamed line) from 2005-10;
RRP = the total accepted in overnight reverse repo operations that day ($, summed over operations; zero before the facility mattered), from 2013-09 (0 earlier).
LIQUIDITY L_d = -(TGA_d + RRP_d), in $ billions (a rise is an injection). g_d = L_d - L_{d-7 calendar days} (forward-filled across weekends); z = (g_d - mean of the previous 250 g) / stdev of the same,
day d excluded, >= 120 prior days.
VARIANTS (K = 2, alpha 0.05/2 = 0.025, one-sided): signals known after the close of date d (both series are published for date d the next morning at the latest; to be conservative the signal for d is used
only from the NEXT trading day's decision: i.e. the value stamped d is applied as if known at the close of d+1).
  L1 FOLLOW LIQUIDITY: z >= +1.0 (injection) -> LONG the 10 index CFDs, 5 days; z <= -1.0 (drain) -> SHORT. L2 FADE: the opposite.
ENGINE: the daily-candle engine of the VT1 replication, PRE 2008-01..2015-12 and POST 2016-01..2026-10, long financing 6.5%/yr and short 2%/yr, +20% spread, one-candle delay, per-index results printed.
EVIDENCE RULE (as the other daily-engine families): SUPPORTED only if mean net R > 0 and +20%-spread mean > 0 in BOTH periods and the pooled date-clustered p < 0.025. Self-test: shared with the VT1 replication engine.
"""
import json
import math
import os
import statistics
import sys
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(__file__))
import research_bond_lead_2026_10_24 as bl
import research_ml_macro_2026_10_25 as mm
import research_vol_surface_replication_2026_10_25 as rep

ALPHA = 0.05 / 2
PRE = ("2008-01-01", "2016-01-01")
POST = ("2016-01-01", "2027-01-01")
H = {"User-Agent": "Mozilla/5.0"}


def get(url):
    for k in range(5):
        try:
            return json.loads(urllib.request.urlopen(urllib.request.Request(url, headers=H), timeout=60).read())
        except Exception as e:
            print("  retry", str(e)[:60], flush=True)
    raise RuntimeError(url)


def fetch_tga():
    out, page = {}, 1
    base = ("https://api.fiscaldata.treasury.gov/services/api/fiscal_service/v1/accounting/dts/operating_cash_balance?"
            "filter=account_type:in:(Federal Reserve Account,Treasury General Account (TGA) Closing Balance)&sort=record_date&page[size]=10000&page[number]=")
    while True:
        d = get(urllib.parse.quote(base, safe=":/?=&,()[]") .replace(" ", "%20") + str(page))
        for r in d["data"]:
            v = r.get("close_today_bal")
            if v not in (None, "null", ""):
                out[r["record_date"]] = float(v)
        if page >= int(d["meta"]["total-pages"]):
            break
        page += 1
    # From 2021-10 the DTS reports the account as an OPENING balance line (open_today_bal, known at the open of date d = the close of the previous day); use it as the value for d
    # (one day staler than a closing balance stamped d would be, which is the conservative direction).
    page = 1
    base2 = ("https://api.fiscaldata.treasury.gov/services/api/fiscal_service/v1/accounting/dts/operating_cash_balance?"
             "filter=account_type:eq:Treasury General Account (TGA) Opening Balance,record_date:gte:2021-09-01&sort=record_date&page[size]=10000&page[number]=")
    while True:
        d = get(urllib.parse.quote(base2, safe=":/?=&,()[]") + str(page))
        for r in d["data"]:
            v = r.get("open_today_bal")
            if v not in (None, "null", "") and r["record_date"] > max(out):
                out[r["record_date"]] = float(v)
        if page >= int(d["meta"]["total-pages"]):
            break
        page += 1
    return out


def fetch_rrp():
    out = defaultdict(float)
    for y in range(2013, 2027):
        d = get(f"https://markets.newyorkfed.org/api/rp/reverserepo/propositions/search.json?startDate={y}-01-01&endDate={y}-12-31")
        for op in d.get("repo", {}).get("operations", []):
            if op.get("operationType") == "Reverse Repo" and op.get("totalAmtAccepted") is not None:
                out[op["operationDate"]] += float(op["totalAmtAccepted"])
    return dict(out)


def liquidity_z(tga, rrp):
    ds = sorted(tga)
    L, last_rrp = {}, 0.0
    for d in ds:
        if d in rrp:
            last_rrp = rrp[d]
        L[d] = -(tga[d] / 1000.0 + last_rrp / 1e9)  # TGA in $ millions -> $ billions; RRP in dollars -> $ billions
    g = {}
    for d in ds:
        p = (datetime.strptime(d, "%Y-%m-%d") - timedelta(days=7)).strftime("%Y-%m-%d")
        for k in range(4):  # nearest available day on or before p
            q = (datetime.strptime(p, "%Y-%m-%d") - timedelta(days=k)).strftime("%Y-%m-%d")
            if q in L:
                g[d] = L[d] - L[q]
                break
    return mm.zseries(g)


def lag_one_trading_day(z, trading_days):
    """Apply the value stamped d at the close of the NEXT trading day (conservative publication lag)."""
    out = {}
    td = sorted(trading_days)
    pos = {d: i for i, d in enumerate(td)}
    for d, v in z.items():
        i = pos.get(d)
        if i is not None and i + 1 < len(td):
            out[td[i + 1]] = v
    return out


def main(d1_dir, cache_dir, ext_dir):
    rep.selftest()
    os.makedirs(cache_dir, exist_ok=True)
    tp, rp_ = os.path.join(cache_dir, "tga.json"), os.path.join(cache_dir, "rrp.json")
    if not os.path.exists(tp):
        json.dump(fetch_tga(), open(tp, "w"))
        json.dump(fetch_rrp(), open(rp_, "w"))
    tga, rrp = json.load(open(tp)), json.load(open(rp_))
    print(f"TGA {min(tga)}..{max(tga)} ({len(tga)} days), RRP {min(rrp)}..{max(rrp)} ({len(rrp)} days)")
    gspc = mm.load_yahoo(os.path.join(ext_dir, "y_GSPC.json"))
    z = lag_one_trading_day(liquidity_z(tga, rrp), gspc)
    dd = {i: [c for c in json.load(open(os.path.join(d1_dir, f"{i}_D_MBA.json"))) if c.get("complete", True)] for i in rep.INDICES}
    m = rep.m
    for name, sign in (("L1 FOLLOW liquidity (injection -> long, drain -> short)", 1), ("L2 FADE liquidity", -1)):
        days = [d for d in sorted(z) if d >= PRE[0] and abs(z[d]) >= 1.0]
        decs = [(d, sign if z[d] > 0 else -sign) for d in days]
        rows, stress, late = [], [], []
        for i in rep.INDICES:
            rows += [dict(r, inst=i) for r in rep.trades(dd[i], decs)]
            stress += rep.trades(dd[i], decs, stress=True)
            late += rep.trades(dd[i], decs, delay=1)
        print(f"\n=== {name}: {len(days)} signal days ===")
        ok = True
        for lab, (a, b) in (("PRE ", PRE), ("POST", POST)):
            sub = rep.per(rows, a, b)
            st = m(rep.per(stress, a, b))
            ok = ok and m(sub) > 0 and st > 0
            longs = [r for r in sub if False]
            print(f"  {lab}: trades {len(sub):5d} mean {m(sub):+.4f} | +20% spread {st:+.4f} | one candle later {m(rep.per(late, a, b)):+.4f}")
        pooled = rep.per(rows, PRE[0], POST[1])
        p = rep.clustered_p(pooled)
        print(f"  pooled: n={len(pooled)} mean {m(pooled):+.4f} date-clustered p={p:.4f} (alpha {ALPHA}) -> {'SUPPORTED' if ok and p < ALPHA else 'NOT SUPPORTED'}")
        yrs = defaultdict(list)
        for r in pooled:
            yrs[r["time"][:4]].append(r["r"])
        print("  by year: " + " ".join(f"{y[2:]}:{statistics.fmean(v):+.2f}" for y, v in sorted(yrs.items())))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], sys.argv[3])
