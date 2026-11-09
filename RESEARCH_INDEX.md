# Research index: everything already tested (read this BEFORE proposing an idea)

Compact verdict list so neither the daily cloud routine nor an interactive session repeats work. Details, numbers and scripts are in `DEVELOPMENT_LOG.md`
(search by the family name) and `scripts/research_*`. Updated 2026-11-10. Standard verdict language: RULED OUT = failed the pre-registered bar; WEAK = positive but not
significant / too rare; REAL-BUT-GONE = a genuine edge that no longer exists. Nothing has passed the strict shipping bar (EVIDENCE_BAR.md) or the practice-trial bar.

## Read this first: what the programme has learned
- About 236 hand-built variants in 115 families plus two exhaustive scans (about 9,500 cells) found no strategy that passes. Published effects that worked before about 2016 are gone since.
- The only strong edges were pricing-feed artefacts, and they closed: OANDA's crypto CFDs lagged Binance/Coinbase by about two hours in 2019-2022 (net +0.2 to +0.9 R per trade, 5-12 trades a week) and the lag has been zero since 2023.
- Costs decide most things: spreads at the 22:00-00:00 UTC rollover are huge; financing dominates low-volatility instruments (bond CFDs, managed currencies); crypto alt CFDs cost about 0.37 R per 2 hours.
- Live forward tests cannot confirm edges of 0.05-0.1 R per trade (hundreds to thousands of trades needed); they only catch disasters.
- Look-ahead bugs have bitten before (daily trend-following 2026-08). Every script needs a random-walk self-test and a peeking-rule self-test.

## A. Price-only rules on FX, indices, commodities (all RULED OUT)
VWAP Scalp and every refinement (filters, sizing, stops, trailing, partial TP; a 2x target replayed on the live trades 2026-10-31: win rate 43.6 to 39.6%, about -0.23 to -0.16 per trade, still losing); ORB Fade; Range Confluence; opening-range breakout, Bollinger fade, Donchian, 1m/15m/60m scalps; the user's triangle + support/resistance + RSI setup; Malaysian SnR (fresh line-chart A/V levels, first-touch fade, with and without the daily storyline filter; 2026-10-29, about -0.19 R per trade, zero-spread -0.01);
H4 and daily time-series momentum (1-252 day lookbacks); daily 200-day trend (the old "edge" was a look-ahead artefact); G10 and EM carry with honest lagged rates; PPP value; rate momentum; cross-sectional momentum and reversal (FX, countries, commodities, 16-instrument ranking);
relative-value pairs (FX, metals, oil; index pairs on daily AND hourly bars); London 4pm fix; Tokyo gotobi days; quarter-end dollar funding; weekend gap fade/follow (FX, crypto); intraday momentum, overnight drift; turn of month; dip buying; options-expiration week; pre-holiday; Halloween (right direction, 2 trades a year, not significant);
week-of-year seasonality (commodities, FX); FX time-of-day and a 432-cell hour-of-day scan on indices/metals/oil/crypto; trend on managed/EM currencies (SGD, CNH, THB, TRY, ZAR, MXN...); trend/reversal on the six bond CFDs; volume climax; round numbers; spread regimes; low-cost universe; index beta and vol-timing.
Cash-session rules on the 13 non-US index CFDs (JP225, HK33, CHINAH, CN50, SG30, AU200, DE30, UK100, FR40, EU50, CH20, NL25, ESPIX; M15, 2026-10-30): intraday momentum
(-0.17 R), overnight premium (-0.08), post-close reversal of the last half-hour (-0.19); before-cost effects +0.005 to +0.03 vs 0.09-0.22 R spread cost. RULED OUT.
Weekly run 2026-10-31 (M15/H1, scripts/research_flow_and_range_2026_10_31.py): leveraged-ETF rebalancing continuation (+0.016, fresh -0.105) and next-morning reversal (-0.108) on US indices; Internal Bar Strength long on 17 indices (-0.052); NR7 narrow-range breakout on 28 instruments (+0.008); A-H premium CHINAH vs CN50 (+0.017, gross +0.158, fresh -0.219). All effectively none. US-only IBS (post-hoc; holdout +0.093, 3.4 trades a week) FAILED its 2006-2015 replication (+0.079, p 0.11; needed p below 0.05): RULED OUT, no more IBS variants.
Daily run 2026-11-02 (scripts/research_reopen_and_cross_lag_2026_11_02.py): Asian reopen under-reaction (follow the US move made while HK33/CHINAH/CN50 were shut, from reopen+30 min to 04:00 or 08:00 UTC; -0.068 / -0.019, gross about 0: the reopen already prices it) RULED OUT.
Daily run 2026-11-03: PBoC daily USD/CNY fixing surprise (fix vs CNH at 01:00 UTC, 60-day z; scripts/research_pboc_fix_surprise_2026_11_03.py) -> follow it in USD_CNH/AUD_USD/NZD_USD 01:30-08:30 UTC (-0.130 R, 9.3/wk), to next day (-0.124), CNH-only |z|>=1 (-0.206): RULED OUT; before costs slightly the WRONG way (a strong fix is followed by CNH weakness: the PBoC leans against the market, the market keeps going).
Weekend Bitcoin move (Binance, Fri-Sun 21:00 UTC) -> same direction in 13 index CFDs and risk FX on Monday 01:00-12:00 / -20:00 UTC (scripts/research_weekend_crypto_lead_2026_11_03.py): holdout +0.107 / +0.094 (p 0.08 / 0.10, only 125 Mondays) but 2017-19 -0.29 and fresh -0.29 / -0.39: WEAK, not credible, no more variants.
Daily run 2026-11-04: Shanghai Gold Exchange premium (SGE AM/PM benchmark vs XAU_USD x USD_CNH, 60-day z; scripts/research_sge_premium_2026_11_04.py) -> follow it in XAU/XAG to 16:00 UTC (-0.057 R, 6.9/wk), to next day (-0.083), PM auction (-0.021); and as a capital-flight signal for USD_CNH/AUD/NZD (scripts/research_sge_premium_fx_2026_11_04.py; -0.098 / -0.104, 10.3/wk). Gross about zero everywhere: RULED OUT.
Daily run 2026-11-05: US Treasury auction RESULT surprise (primary-dealer take-up share or bid-to-cover vs the previous 6 same-term auctions; fiscaldata; scripts/research_auction_surprise_2026_11_05.py) -> weak auction short USB05Y/10Y/30Y, long USD_JPY to 15:45 ET (-0.264 R, 5.7/wk), to next day (-0.135), bid-to-cover (-0.197); gross slightly the wrong way (the concession is paid before the result): RULED OUT.
Daily run 2026-11-06: NON-US scheduled-release shocks (9 fixed local slots: Sydney 11:30 AUD, Wellington 10:45 NZD, Tokyo 08:50 JPY, London 07:00 GBP, Frankfurt 10:00/11:00 EUR, Zurich 08:30 CHF, Stockholm/Oslo 08:00 SEK/NOK; release-bar move >= 2x usual; scripts/research_nonus_news_shock_2026_11_06.py): follow 4h (-0.084 R, 9.4/wk), fade 4h (-0.113); gross about +-0.01: RULED OUT. With the US 8:30 test, scheduled-release timing rules are exhausted.
Daily run 2026-11-09: FX OPTION-EXPIRY PINNING at the 10:00 New York cut (DTCC public option trades, new data; biggest expiring strike cluster >= USD 1B within 0.05-0.5 daily moves of spot, trade toward it; scripts/research_fx_option_pin_2026_11_09.py): 08:00-10:00 NY (-0.018 R, 6.9/wk, fresh -0.24), 06:00-10:00 NY (-0.048); gross flips sign by period and pair: RULED OUT.
Daily run 2026-11-10: FX OPTION ORDER FLOW (DTCC public option trades: previous day's base-currency call minus put USD notional imbalance, 60-day z >= 0.5, follow it in the 7 USD majors from 07:00 UTC; scripts/research_fx_option_flow_2026_11_10.py): to next day (-0.033 R, 21/wk, fresh -0.13), to 16:00 UTC (-0.021, fresh -0.18); gross flips sign by pair and period: RULED OUT.
CFD roll adjustments (read-only account transactions 2026): no adjustment or dividend transaction exists because the bot never holds a CFD across a roll; question stays open (needs an OANDA-vs-futures-curve comparison).
Reasoned out without a test (2026-10-30): GSCI roll pressure (the effect is in the calendar spread, which one CFD per commodity cannot trade); OANDA CFD roll gaps (most likely offset by a cash adjustment invisible in candles; unverified).

## A2. Social-media strategies (Instagram / TikTok / YouTube; a source first used 2026-10-29) -- all RULED OUT
Malaysian SnR fresh-level first touch (with/without storyline); ICT fair value gap (M15, daily bias); ICT order block + break of structure (M15, daily bias); liquidity sweep of
the Asian range (M15); candle range theory, CRT (H4); Quasimodo, QM (H4). Every one loses 0.16-0.36 R per trade after costs in every year and on all 17 instruments, and
none has a before-cost edge beyond +0.05 R (after removing the test engine's own random-walk bias). They trade very often on tight stops, so the spread alone is about 0.2 R.
TikTok indicator recipes (2026-10-29, all RULED OUT, -0.09 to -0.34 R after costs, before-cost edge at most +0.07): MACD + 200 EMA; Supertrend flip + 200 EMA;
RSI regular divergence; Heikin Ashi flip + EMA50; 3-EMA + Stochastic RSI scalp; ICT Silver Bullet (on M15, not M1-M5).
Claude "day-trader-skill" pack (2026-11-01; US-stock tooling, no edge claimed): its scanner's bull/bear flag (8-bar pole >= 3 ATR, tight shallow flag, breakout,
measured-move target) RULED OUT: M15 -0.20 R, H1 -0.12 R after costs; before costs worse than on random prices. Its ORB and oversold-bounce setups were already ruled out.
Untested from this source (send the exact rules if wanted): SMC "CHoCH" entries, MSnR RBS/SBR flips and engulfing confirmation, power of 3 / AMD.

## B. Scheduled events and news (RULED OUT)
US 8:30 ET news-shock continuation; pre-FOMC drift (46 events, underpowered); weekly EIA oil (Wed) and natural-gas (Thu) report reactions; Treasury auction cycle; jobless claims momentum.
Month-end equity-vs-bond pension rebalancing on 16 index CFDs + 7 USD majors (2026-10-29, M1): WEAK -- holdout +0.028 (n 6404, p 0.16), fresh +0.149, meets the practice-trial bar on paper, but a one-day delay is ~0, two days negative, and a pre-registered 1990-2002 replication FAILED (gross +0.014 vs about 0.057 cost).

## C. OANDA retail positioning data (RULED OUT)
CFTC COT speculator crowding, contrarian, on 24 futures markets incl. commodities, platinum/palladium and equity indices (2026-10-29, P1: holdout -0.041, n 6067; the 2026-08-29 FX-only test was weak too).
Position book and order book signals (crowd fade, stop magnet, breakout pressure; 8 variants), stop-magnet dose-response, limit-order adverse selection, walk-forward ML on all of it.

## D. Outside data, equities / FX / commodities (RULED OUT)
Bond-CFD lead into FX/indices; VIX family (VIX/VIX3M, VIX9D/VIX, VVIX, SKEW), MOVE, credit (HYG/LQD), OVX/GVZ spikes as stress-rebound signals; variance risk premium (index and commodity); NFCI (with real-time vintages); US net liquidity (TGA + reverse repo);
2-year yield changes; Hau-Rey rebalancing; commodity-currency catch-up; walk-forward ML on 17 macro/stress features x 28 instruments; an exhaustive scan of 69 ETF/volatility/rates series x 4 transforms x 28 instruments (nothing selected).
WEAK: **VT1** (VIX/VIX3M >= 1.0 -> long indices 5 days): positive in 2009-15 and 2016-26 with a clean dose-response cliff at 1.0, but only 3-4 episodes a year, p about 0.14-0.19, and large losses in prolonged crashes.

## E. Crypto CFDs (BTC/ETH/LTC/BCH): mostly RULED OUT
Momentum/reversal; Binance funding rate; Fear & Greed; taker-buy flow; Coinbase premium; DeFi TVL; Wikipedia attention; on-chain addresses; hash ribbons; Deribit DVOL; hourly taker flow; BTC-leads-alts; an exhaustive crypto scan (1,550 cells, nothing selected).
WEAK: the post-hoc composite (funding + taker share + Coinbase premium; now the live **Crypto Flow** experiment) and **SC1** (stablecoin-supply growth follow; +0.345 R holdout, p 0.025, weak since Oct 2025, about 0.8 trades a week).
REAL-BUT-GONE: **cross-venue gap convergence** (Binance or Coinbase price above the OANDA crypto CFD -> CFD catches up within 2 hours): net +0.2 to +0.9 R in 2019-2022, gross zero from 2023.
Cross-venue on gold (PAXG) and the euro (EURUSDT): nothing (OANDA leads).

## F. Pricing-consistency / arbitrage / venue-lag checks (RULED OUT: OANDA quotes are consistent and no longer lag)
Seven gold crosses vs XAU_USD x FX; Nikkei dollar vs yen identity; related-index hourly ratios; hourly OANDA index / metal / oil / FX CFDs vs the CME futures and spot feeds on Yahoo (2024-06..2026-10 only): the index CFDs lagged ES/NQ/YM/RTY in 2024 (gross +0.19 R in 2024Q2, fading to 0 by 2025Q1, negative since); metals, oil and FX never did.
Venue lag on the CFDs the 2026-10-28 test missed (Yahoo hourly futures 2024-06..2026-11, scripts/research_venue_lag_other_2026_11_05.py): ags CORN/WHEAT/SOYBN/SUGAR (-0.50 R), NATGAS/BCO/XPT/XPD (-0.33), JP225_USD/JP225Y_JPY vs CME Nikkei (-0.09); gross about 0 everywhere: no lag. RULED OUT.
Exotic/minor FX cross staleness vs the cross implied by its USD legs (48 crosses, H1, 2026-11-02): gross +0.02 vs -0.64 net. RULED OUT.

## G. Ideas NOT yet tried (candidates; check the log before starting)
Fed balance-sheet / reserves (WRESBAL) with vintages; GDPNow revisions; breakeven inflation (T5YIE); month-end pension rebalancing; central-bank meeting dates other than the Fed; weather vs natural gas; USDA/agriculture reports; Asian/European index CFDs
(HK33, CN50, SG30, CH20, NL25, ESPIX, CHINAH) now used in M1, P1 and the 2026-10-30 session rules; platinum/palladium only in M1 and P1; A-H premium (CHINAH vs CN50); non-Fed central-bank pre-meeting drift; VT1 applied to risk FX crosses (AUD_JPY, NZD_JPY); intraday outside data for non-crypto markets (needs paid feeds); options flow/gamma (paid). (COT done 2026-10-29: the Socrata API publicreporting.cftc.gov works from the cloud; the cftc.gov zip files return a Cloudflare 403.)

## Data access notes
- Reachable from the local Windows machine: Binance (data-api.binance.vision, www.binance.com funding), Coinbase, DeFiLlama, Deribit, alternative.me, blockchain.com, Wikimedia, CBOE CSVs (follow 307 redirects), Yahoo Finance chart API, Treasury FiscalData, NY Fed, World Bank, ECB data API.
  NOT reachable locally: FRED, CFTC, GDELT/AAII/CNN endpoints. The cloud sandbox reaches FRED/ALFRED (real-time vintages) but its IP is refused by fapi.binance.com (HTTP 451).
- SGE gold benchmark history (AM/PM, 2016-): www.sge.com.cn/graph/DayilyJzj works from the cloud. spdrgoldshares.com (GLD holdings) is behind a Cloudflare bot check.
- DTCC public swap data (pddata.dtcc.com/ppd/api/report/cumulative/cftc/CFTC_CUMULATIVE_FOREX_YYYY_MM_DD.zip; also COMMODITIES/EQUITIES/RATES) works from the cloud; history only from about 2024-12.
- Open-Meteo archive/previous-runs APIs hit a shared daily quota from the cloud (unreliable); farside.co.uk returns 403; data.snb.ch API works.
- PBoC USD/CNY fixing history: safe.gov.cn/AppStructured/hlw/RMBQuery.do (POST startDate/endDate/queryYN=true, quarterly chunks, CNY per 100 USD) works from the cloud; chinamoney.com.cn answers a few calls then returns 403. Binance spot klines (data-api.binance.vision) work from the cloud.
- OANDA instruments: 68 FX pairs, 38 CFDs (indices, bonds, ags, energy, crypto), 21 metals. Practice-account financing per instrument is in the instruments endpoint.
