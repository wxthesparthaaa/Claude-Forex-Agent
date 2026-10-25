"""
Research, 2026-10-25. PRE-REGISTERED before any result was seen. RELATIVE VALUE BETWEEN INDEX CFDs: the 2026-10-24 pairs test covered currencies, metals and oil only. Indices that share an underlying economy or region (the S&P 500 against the Dow and the
Nasdaq and the Russell 2000; the DAX against the Euro Stoxx 50 and the CAC 40) are tied by arbitrage-like constituent overlap, so their ratio should mean-revert, and an index pair is hedged against the common market move. Same rule, parameters and evaluator as
research_relative_value_2026_10_24.py (OLS of log A on log B over a 120-bar window, trade when the residual z reaches Z, exit at z = 0 or 30 bars) applied to DAILY candles (so 120 days of window and at most 30 days of hold): pairs fixed in advance
SPX500/US30, SPX500/NAS100, SPX500/US2000, DE30/EU50, DE30/FR40, EU50/FR40; Z = 2.0 and 2.5 (K = 2, alpha 0.05/2 = 0.025); financing charged at 8.5%/yr of the notional (6.5% on the long leg plus 2% on the short, rounded). The relative-value module is
imported unchanged, so its look-ahead self-test is the one already passed. Daily bars are stored under the *_H4_MBA.json names the module expects; its printed 'avg hold h' therefore reads in days x 4.
DISCOVERY < 2021-01-01 <= HOLDOUT < 2025-10-01 <= FRESH (the module's periods); the full checks are printed by the module.
"""
import importlib.util
import os
import sys

HERE = os.path.dirname(__file__)
_spec = importlib.util.spec_from_file_location("rv", os.path.join(HERE, "research_relative_value_2026_10_24.py"))
rv = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rv)

rv.PAIRS = [("SPX500_USD", "US30_USD"), ("SPX500_USD", "NAS100_USD"), ("SPX500_USD", "US2000_USD"),
            ("DE30_EUR", "EU50_EUR"), ("DE30_EUR", "FR40_EUR"), ("EU50_EUR", "FR40_EUR")]
rv.FIN = 0.085
_orig_trades = rv.trades
rv.trades = lambda A, B, Z, delay=0, stress=False, fin=0.085: _orig_trades(A, B, Z, delay=delay, stress=stress, fin=fin)

if __name__ == "__main__":
    rv.main(sys.argv[1])
