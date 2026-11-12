"""
Research, 2026-11-13 (daily research routine). PRE-REGISTERED before any result was seen.
VENUE LAG on the last OANDA CFDs/pairs with a liquid CME futures twin that no venue-lag test has covered: the four US Treasury bond CFDs and the
five non-EUR/GBP USD majors (RESEARCH_INDEX section F: 2026-10-28 covered US indices, GC/SI/HG, CL, EUR, GBP; 2026-11-05 covered ags, NG, Brent,
XPT/XPD, Nikkei). Mechanism (one sentence): if OANDA prices a CFD from a slower or stale feed of its underlying, the CFD trails the futures after a
move and trading the CFD toward the futures captures the catch-up (as on OANDA's crypto CFDs 2019-2022 and its US index CFDs in 2024).
PAIRS (OANDA H1 mid close vs the Yahoo hourly close of the same UTC hour, Yahoo range=730d, i.e. 2024-06..2026-11):
  V1 US TREASURY CFDs: ZT=F -> USB02Y_USD, ZF=F -> USB05Y_USD, ZN=F -> USB10Y_USD, ZB=F -> USB30Y_USD
  V2 FX MAJORS: 6J=F -> USD_JPY, 6A=F -> AUD_USD, 6C=F -> USD_CAD, 6S=F -> USD_CHF, 6N=F -> NZD_USD
  (6J/6C/6S are quoted as USD per foreign unit, so the fetcher stores 1/close for them; a pure transform fixed before any outcome.)
  UK10YB/DE10YB are not tested: Yahoo has no Eurex/ICE bund/gilt futures (FGBL=F returns nothing).
SIGNAL, ENGINE, RISK UNIT, CHECKS: identical to scripts/research_futures_venue_lag_2026_10_28.py and reused unchanged (g = ln(Yahoo / OANDA mid),
z vs previous 336 common hours (>= 200 prior, this hour excluded), |z| >= 2 -> trade toward the futures at the OPEN of the next OANDA H1 bar, hold
2 bars, non-overlapping, real bid/ask, +20% spread and one-bar delay, date-clustered one-sided t-test, financing ignored for 2 hours).
Periods: before 2025-10-01 = 'holdout', 2025-10-01.. = 'fresh'; no discovery period (only 730 days of free hourly data; stated up front).
K = 2 groups, alpha 0.025, pooled within group; per-instrument printed, not selected on. Expected trades per week: earlier runs gave 1.3-1.6 per
instrument per week at |z| >= 2 -> about 5-6 for V1, 6-8 for V2 (meets the ~5 a week requirement).
DECISION: CREDIBLE (strict) only if all evaluator checks pass; practice-trial grade printed by rp.practice_flag. Self-test: ch.selftest.
Run: python3 scripts/research_venue_lag_bonds_fx_2026_11_13.py /tmp/research/h1 /tmp/research/derivs
"""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
import research_futures_venue_lag_2026_10_28 as vl28

vl28.bl.ALPHA = 0.05 / 2
vl28.GROUPS = {
    "V1 US TREASURY CFDs vs CBOT futures": [("USB02Y_USD", "ZT_F"), ("USB05Y_USD", "ZF_F"), ("USB10Y_USD", "ZN_F"), ("USB30Y_USD", "ZB_F")],
    "V2 FX MAJORS vs CME FX futures": [("USD_JPY", "6J_F"), ("AUD_USD", "6A_F"), ("USD_CAD", "6C_F"), ("USD_CHF", "6S_F"), ("NZD_USD", "6N_F")],
}

if __name__ == "__main__":
    vl28.main(sys.argv[1], sys.argv[2])
