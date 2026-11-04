"""
Research, 2026-11-05 (daily research routine). PRE-REGISTERED before any result was seen.
VENUE LAG on the OANDA CFDs that the 2026-10-28 venue-lag test (scripts/research_futures_venue_lag_2026_10_28.py) did NOT cover: agricultural,
other energy, platinum-group metals and the Nikkei CFDs (RESEARCH_INDEX section F lists only indices ES/NQ/YM/RTY, GC/SI/HG, CL and EUR/GBP).
Mechanism (one sentence): if OANDA prices a thinly traded CFD from a slower or stale feed of its underlying futures contract, the CFD trails the
futures for a while after a move, and trading the CFD toward the futures price captures the catch-up (this is exactly what happened on OANDA's crypto
CFDs in 2019-2022 and on its US index CFDs in 2024).
PAIRS (OANDA H1 mid close vs the Yahoo Finance hourly close of the SAME UTC hour; Yahoo bars stamped at the start of the hour, only bars on the hour
with a non-null close; the last, possibly incomplete, Yahoo bar dropped):
  M1 AGRICULTURE: ZC=F -> CORN_USD, ZW=F -> WHEAT_USD, ZS=F -> SOYBN_USD, SB=F -> SUGAR_USD
  M2 OTHER ENERGY AND PLATINUM GROUP: NG=F -> NATGAS_USD, BZ=F -> BCO_USD, PL=F -> XPT_USD, PA=F -> XPD_USD
  M3 NIKKEI: NKD=F (CME dollar Nikkei) -> JP225_USD, NIY=F (CME yen Nikkei) -> JP225Y_JPY
SIGNAL, ENGINE, RISK UNIT, CHECKS: identical to the 2026-10-28 script and reused from it unchanged (g = ln(Yahoo close / OANDA mid close),
z vs the previous 336 common hours (>= 200 prior, this hour excluded), |z| >= 2 -> trade the CFD toward the futures at the OPEN of the next OANDA H1
bar, hold 2 bars, non-overlapping, real bid/ask, +20% spread and one-bar delay, date-clustered one-sided t-test, financing ignored for 2 hours).
Only 730 days of free hourly data exist: the whole sample is 2024-06..2026-11; before 2025-10-01 = 'holdout', 2025-10-01.. = 'fresh', no discovery.
K = 3 groups, alpha 0.05/3 = 0.0167, pooled within group, per-instrument results printed and not selected on. Expected trades per week: the
2026-10-28 run gave 1.3-1.6 per instrument per week at |z| >= 2, so about 5-6 for M1 and M2, about 3 for M3 (below the 5-a-week bar; stated now).
DECISION: CREDIBLE (strict) only if all evaluator checks pass; practice-trial grade printed separately by rp.practice_flag. By-quarter gross is
descriptive (is any lag fading?). Self-test: ch.selftest (random walk and peeking) from the crypto hourly script.
Data fetch (done first, separately): Yahoo -> /tmp/research/derivs/yh1_<SYM with = replaced by _>.json as [[unix_ts, close], ...];
OANDA H1 via scripts/research_h1_fetch_2026_10_24.py with START 2024-05-01 into /tmp/research/h1.
Run: python3 scripts/research_venue_lag_other_2026_11_05.py /tmp/research/h1 /tmp/research/derivs
"""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
import research_futures_venue_lag_2026_10_28 as vl28

vl28.bl.ALPHA = 0.05 / 3
vl28.GROUPS = {
    "M1 AGRICULTURE vs CBOT/ICE futures": [("CORN_USD", "ZC_F"), ("WHEAT_USD", "ZW_F"), ("SOYBN_USD", "ZS_F"), ("SUGAR_USD", "SB_F")],
    "M2 NATGAS, BRENT, PLATINUM, PALLADIUM vs futures": [("NATGAS_USD", "NG_F"), ("BCO_USD", "BZ_F"), ("XPT_USD", "PL_F"), ("XPD_USD", "PA_F")],
    "M3 NIKKEI CFDs vs CME Nikkei futures": [("JP225_USD", "NKD_F"), ("JP225Y_JPY", "NIY_F")],
}

if __name__ == "__main__":
    vl28.main(sys.argv[1], sys.argv[2])
