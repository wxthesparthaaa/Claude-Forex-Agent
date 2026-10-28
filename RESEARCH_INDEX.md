# Research index: everything already tested (read this BEFORE proposing an idea)

Compact verdict list so neither the daily cloud routine nor an interactive session repeats work. Details, numbers and scripts are in `DEVELOPMENT_LOG.md`
(search by the family name) and `scripts/research_*`. Updated 2026-10-28. Standard verdict language: RULED OUT = failed the pre-registered bar; WEAK = positive but not
significant / too rare; REAL-BUT-GONE = a genuine edge that no longer exists. Nothing has passed the strict shipping bar (EVIDENCE_BAR.md) or the practice-trial bar.

## Read this first: what the programme has learned
- About 175 hand-built variants in 80 families plus two exhaustive scans (about 9,500 cells) found no strategy that passes. Published effects that worked before about 2016 are gone since.
- The only strong edges were pricing-feed artefacts, and they closed: OANDA's crypto CFDs lagged Binance/Coinbase by about two hours in 2019-2022 (net +0.2 to +0.9 R per trade, 5-12 trades a week) and the lag has been zero since 2023.
- Costs decide most things: spreads at the 22:00-00:00 UTC rollover are huge; financing dominates low-volatility instruments (bond CFDs, managed currencies); crypto alt CFDs cost about 0.37 R per 2 hours.
- Live forward tests cannot confirm edges of 0.05-0.1 R per trade (hundreds to thousands of trades needed); they only catch disasters.
- Look-ahead bugs have bitten before (daily trend-following 2026-08). Every script needs a random-walk self-test and a peeking-rule self-test.

## A. Price-only rules on FX, indices, commodities (all RULED OUT)
VWAP Scalp and every refinement (filters, sizing, stops, trailing, partial TP); ORB Fade; Range Confluence; opening-range breakout, Bollinger fade, Donchian, 1m/15m/60m scalps; the user's triangle + support/resistance + RSI setup;
H4 and daily time-series momentum (1-252 day lookbacks); daily 200-day trend (the old "edge" was a look-ahead artefact); G10 and EM carry with honest lagged rates; PPP value; rate momentum; cross-sectional momentum and reversal (FX, countries, commodities, 16-instrument ranking);
relative-value pairs (FX, metals, oil; index pairs on daily AND hourly bars); London 4pm fix; Tokyo gotobi days; quarter-end dollar funding; weekend gap fade/follow (FX, crypto); intraday momentum, overnight drift; turn of month; dip buying; options-expiration week; pre-holiday; Halloween (right direction, 2 trades a year, not significant);
week-of-year seasonality (commodities, FX); FX time-of-day and a 432-cell hour-of-day scan on indices/metals/oil/crypto; trend on managed/EM currencies (SGD, CNH, THB, TRY, ZAR, MXN...); trend/reversal on the six bond CFDs; volume climax; round numbers; spread regimes; low-cost universe; index beta and vol-timing.

## B. Scheduled events and news (RULED OUT)
US 8:30 ET news-shock continuation; pre-FOMC drift (46 events, underpowered); weekly EIA oil (Wed) and natural-gas (Thu) report reactions; Treasury auction cycle; jobless claims momentum.

## C. OANDA retail positioning data (RULED OUT)
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

## F. Pricing-consistency / arbitrage checks (RULED OUT: OANDA quotes are consistent)
Seven gold crosses vs XAU_USD x FX; Nikkei dollar vs yen identity; related-index hourly ratios.

## G. Ideas NOT yet tried (candidates; check the log before starting)
Fed balance-sheet / reserves (WRESBAL) with vintages; GDPNow revisions; breakeven inflation (T5YIE); month-end pension rebalancing; central-bank meeting dates other than the Fed; weather vs natural gas; USDA/agriculture reports; Asian/European index CFDs
not yet in any test (HK33, CN50, SG30, CH20, NL25, ESPIX); platinum/palladium; VT1 applied to risk FX crosses (AUD_JPY, NZD_JPY); intraday outside data for non-crypto markets (needs paid feeds); options flow/gamma (paid); COT positioning (the CFTC site is unreachable locally; may work from the cloud).

## Data access notes
- Reachable from the local Windows machine: Binance (data-api.binance.vision, www.binance.com funding), Coinbase, DeFiLlama, Deribit, alternative.me, blockchain.com, Wikimedia, CBOE CSVs (follow 307 redirects), Yahoo Finance chart API, Treasury FiscalData, NY Fed, World Bank, ECB data API.
  NOT reachable locally: FRED, CFTC, GDELT/AAII/CNN endpoints. The cloud sandbox reaches FRED/ALFRED (real-time vintages) but its IP is refused by fapi.binance.com (HTTP 451).
- OANDA instruments: 68 FX pairs, 38 CFDs (indices, bonds, ags, energy, crypto), 21 metals. Practice-account financing per instrument is in the instruments endpoint.
