"""
Research, 2026-10-28. PRE-REGISTERED before any result was seen. VENUE LAG on the non-crypto markets. The one strong edge of the programme was a feed lag: until 2022 OANDA's crypto CFDs trailed Binance and Coinbase by about two hours and caught up (research_crypto_hourly /
research_crypto_coinbase_gap, DEVELOPMENT_LOG 2026-10-25). OANDA's index, metal, oil and FX CFDs are derived from CME futures and the interbank market; this asks whether any of them trail those venues today, hour by hour. Only 730 days of free HOURLY data exist (Yahoo
Finance, bars stamped at the start of the UTC hour; futures trade about 23 hours a day), so the whole sample is 2024-06..2026-10, stated up front.
PAIRS (OANDA H1 mid close vs the Yahoo hourly close of the SAME UTC hour): indices ES=F -> SPX500_USD, NQ=F -> NAS100_USD, YM=F -> US30_USD, RTY=F -> US2000_USD; metals GC=F -> XAU_USD, SI=F -> XAG_USD, HG=F -> XCU_USD; energy CL=F -> WTICO_USD;
FX 6E=F and EURUSD=X -> EUR_USD, 6B=F and GBPUSD=X -> GBP_USD.
SIGNAL at the close of UTC hour h: g_h = ln(Yahoo close / OANDA mid close); z = (g_h - mean of the previous 336 hours' g) / stdev of the same (hour h excluded, >= 200 prior); the 336-hour (two-week) window lets the futures basis and the quarterly contract roll wash out within about a week.
If |z| >= 2 trade the CFD toward the outside venue (outside above -> LONG) at the OPEN of the next OANDA H1 bar (the bar must start exactly one hour after h), fill side, hold 2 bars, non-overlapping per instrument; R = net P&L / (stdev of the previous 120 H1 mid
log returns x sqrt(2) x entry); financing ignored (2 hours); +20% spread and a one-bar delay; date-clustered one-sided t-test.
VARIANTS (K = 4, alpha 0.05/4 = 0.0125), pooled within group, per-instrument results printed and not selected on: L1 INDICES (4 pairs), L2 METALS (3), L3 ENERGY (WTI), L4 FX (4 pairs). PERIODS: all bars before 2025-10-01 play the evaluator's 'holdout' (about 16 months),
2025-10-01.. is 'fresh'; there is no discovery period and that check is ignored. Self-test shared with the crypto hourly script. CREDIBLE (strict) only if ALL evaluator checks pass; the 5-trades-a-week bar is plausible here. Descriptive, no claim: the same by quarter, to see whether any lag is fading.
"""
import json
import math
import os
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(__file__))
import research_bond_lead_2026_10_24 as bl
import research_retail_positioning_2026_10_25 as rp
import research_crypto_hourly_2026_10_25 as ch

ch.WINDOW, ch.MIN_PRIOR = 336, 200
bl.ALPHA = 0.05 / 4
GROUPS = {
    "L1 INDICES vs CME futures": [("SPX500_USD", "ES_F"), ("NAS100_USD", "NQ_F"), ("US30_USD", "YM_F"), ("US2000_USD", "RTY_F")],
    "L2 METALS vs CME futures": [("XAU_USD", "GC_F"), ("XAG_USD", "SI_F"), ("XCU_USD", "HG_F")],
    "L3 ENERGY vs CME crude": [("WTICO_USD", "CL_F")],
    "L4 FX vs CME futures and spot": [("EUR_USD", "6E_F"), ("EUR_USD", "EURUSD_X"), ("GBP_USD", "6B_F"), ("GBP_USD", "GBPUSD_X")],
}


def hour_key(ts):
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H")


def main(h1_dir, derivs_dir):
    ch.selftest()
    cache = {}
    for name, pairs in GROUPS.items():
        rows = {"base": [], "stress": [], "late": [], "gross": []}
        for oanda, ysym in pairs:
            cs = cache.setdefault(oanda, bl.load(h1_dir, oanda))
            outside = {hour_key(t): c for t, c in json.load(open(os.path.join(derivs_dir, f"yh1_{ysym}.json"))) if c > 0}
            series = [(c["time"][:13], math.log(outside[c["time"][:13]] / float(c["mid"]["c"]))) for c in cs if c["time"][:13] in outside]
            z = ch.rolling_z(series)
            decs = ch.decisions_from_z(z, lambda v: 1 if v >= 2 else (-1 if v <= -2 else 0))
            gs = [v for _, v in series]
            tag = f"{oanda[:7]}~{ysym}"
            n_sig = len(decs)
            for key, kw, src in (("base", {}, cs), ("stress", {"stress": True}, cs), ("late", {"delay": 1}, cs), ("gross", {"fin": False}, bl.mid_only(cs))):
                rows[key] += [dict(r, inst=tag) for r in ch.hourly_trades(oanda, src, decs, 2, **kw)]
            print(f"  {tag}: {len(series)} common hours, gap median {statistics.median(gs) * 1e4:+.1f} bp, sd {statistics.pstdev(gs) * 1e4:.1f} bp, {n_sig} signals")
        bl.evaluate(name, rows["base"], rows["stress"], rows["late"], rows["gross"])
        rp.practice_flag(rows["base"], rows["stress"], rows["late"])
        byq = defaultdict(lambda: ([], []))
        for r in rows["base"]:
            q = r["time"][:4] + "Q" + str((int(r["time"][5:7]) - 1) // 3 + 1)
            byq[q][0].append(r["r"])
        for r in rows["gross"]:
            q = r["time"][:4] + "Q" + str((int(r["time"][5:7]) - 1) // 3 + 1)
            byq[q][1].append(r["r"])
        print("  by quarter net/gross (n): " + "  ".join(f"{q} {statistics.fmean(v[0]):+.2f}/{statistics.fmean(v[1]) if v[1] else float('nan'):+.2f} ({len(v[0])})" for q, v in sorted(byq.items())))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
