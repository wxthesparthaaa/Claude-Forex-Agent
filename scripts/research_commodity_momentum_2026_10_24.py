"""
New-strategy research, run 1l (2026-10-24): CROSS-SECTIONAL commodity momentum. PRE-REGISTERED before results.

Mechanism: commodities that outperformed the others over the past year keep outperforming for the next month or so
(Erb and Harvey 2006; Miffre and Rallis 2007; Asness, Moskowitz and Pedersen 2013) -- inventories and supply respond
slowly, so price trends driven by supply/demand imbalances persist. The 12 OANDA commodity CFDs are fixed in advance:
XAU XAG XPT XPD XCU WTICO BCO NATGAS CORN WHEAT SOYBN SUGAR (all vs USD), daily bid/ask 2005..2026-10-22.
Rule (CM1): identical to scripts/research_country_momentum_2026_10_24.py (12-1 month ranking, long top 3 / short
bottom 3, monthly, fill-side entries/exits at the month's first open, 6.5%/yr long and 2%/yr short financing, same
R definition, same periods and credibility rules), applied to the commodity set instead of the indices.
Running count: 26 before, 27 after; alpha = 0.05 / 27 = 0.00185.
"""
import importlib.util
import os
import sys

ROOT = os.path.join(os.path.dirname(__file__), "..")
_spec = importlib.util.spec_from_file_location("cm", os.path.join(ROOT, "scripts", "research_country_momentum_2026_10_24.py"))
cm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cm)

if __name__ == "__main__":
    cm.tom.INDICES = ["XAU_USD", "XAG_USD", "XPT_USD", "XPD_USD", "XCU_USD", "WTICO_USD", "BCO_USD", "NATGAS_USD",
                      "CORN_USD", "WHEAT_USD", "SOYBN_USD", "SUGAR_USD"]
    cm.ALPHA = 0.05 / 27
    cm.main(sys.argv[1])
