"""
Research, 2026-10-25. PRE-REGISTERED before it was run. Replication of X1 (research_crypto_2026_10_25.py: 20-trading-day
momentum, hold 5 days) on instruments the rule was never looked at on: LTC_USD and BCH_USD (OANDA H1 from 2020-01-02).
The rule, thresholds, execution, costs and evaluator are IDENTICAL to X1; nothing is re-tuned. Financing from the API
(2026-10-25): both pay 28.88%/yr long and 21.12%/yr short. These are the most expensive crypto CFDs to trade (spread/daily
vol 0.26 for LTC, 0.10 for BCH), so a pass here would be a hard one.

K = 1 (alpha 0.05, one-sided, date-clustered). REPLICATED only if ALL of: pooled holdout mean R > 0, each instrument's holdout
mean R > 0, pooled fresh mean R > 0, and holdout still > 0 with +20% spreads and with a one-bar-delayed entry. Anything else is
NOT REPLICATED. Discovery is only 2020 for these two, so it is shown but not required. The always-long control is printed for the
same instruments (a rule that is just 'long' earns crypto beta).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
import research_bond_lead_2026_10_24 as bl
import research_crypto_2026_10_25 as cr
import research_retail_positioning_2026_10_25 as rp

NEW = ["LTC_USD", "BCH_USD"]
cr.RATES.update({i: (0.2888, 0.2112) for i in NEW})
bl.ALPHA = 0.05


def collect(data, fam, **kw):
    return [r for i in NEW for r in cr.trades(i, data[i], fam, cr.decisions(i, data[i], fam), **kw)]


def main(h1_dir):
    cr.selftest()
    data = {i: bl.load(h1_dir, i) for i in NEW}
    for i in NEW:
        print(f"{i}: {len(data[i])} bars {data[i][0]['time'][:10]}..{data[i][-1]['time'][:10]}")
    for fam, label in (("CONTROL", "CONTROL always long (benchmark)"), ("X1", "X1 momentum 20-day / hold 5 days (REPLICATION)")):
        base = collect(data, fam)
        stress = collect(data, fam, stress=True)
        delayed = collect(data, fam, delay=1)
        gross = [r for i in NEW for r in cr.trades(i, bl.mid_only(data[i]), fam, cr.decisions(i, data[i], fam), fin=False)]
        bl.evaluate(label, base, stress, delayed, gross)
        if fam == "X1":
            m = lambda rows, k: bl._m([r["r"] for r in rows if bl._period(r["time"]) == k])
            per = {i: bl._m([r["r"] for r in base if r["inst"] == i and bl._period(r["time"]) == "holdout"]) for i in NEW}
            ok = (m(base, "holdout") > 0 and all(v > 0 for v in per.values()) and m(base, "fresh") > 0
                  and m(stress, "holdout") > 0 and m(delayed, "holdout") > 0)
            print(f"\nREPLICATION VERDICT: {'REPLICATED' if ok else 'NOT REPLICATED'} (pooled holdout {m(base, 'holdout'):+.4f}, "
                  f"per instrument {per}, fresh {m(base, 'fresh'):+.4f}, +20% spread {m(stress, 'holdout'):+.4f}, delayed {m(delayed, 'holdout'):+.4f})")


if __name__ == "__main__":
    main(sys.argv[1])
