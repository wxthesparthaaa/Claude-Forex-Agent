"""
Research, 2026-11-27. PRE-REGISTERED before any result was seen (and before the data were downloaded). REPLICATION of the US-open RSI rule on markets never used to find it. The rule (research_rsi_open_improve_2026_11_27.py baseline): RSI(14) on 5-minute bars of the mid close crosses above the upper line -> LONG, below the lower line -> SHORT, signal bar starting in the first 30 minutes of the
local cash session; entry at the next open at the real ask / bid; hard stop 1.5 x ATR14 from the fill; exit when RSI crosses back through 50 against the trade or at the session's last bar; one position per index; real bid/ask 2016-2026.
NEW DATA (fetched today by scripts/research_bidask_fetch_2026_11_27.py, 5-minute bid / ask / tick volume 2016-01..2026-11-24): US2000_USD (a US small-cap index, same New York session 09:30-16:00), and the SEVEN European indices at 5-minute resolution (DE30, UK100, FR40, EU50, NL25, ESPIX, CH20; the earlier test used 15-minute bars for them), each at its own cash-session open (sessions as in research_rsi_opening_sessions_2026_11_27.py).
CELLS, K = 4, alpha 0.05 / 4 = 0.0125 (one-sided): US2000 with lines 70/30; US2000 with 75/25; EUROPE (7 indices pooled) 70/30; EUROPE 75/25.
VERDICT per cell (fixed now): GROSS EDGE only if the pooled gross mean R (zero spread, date-clustered) > 0 with p < 0.0125, > 0 in discovery (2016-2020) and holdout (2021-01..2025-09), > 0 for longs and shorts separately and > 0 on a majority of the instruments separately. PROFITABLE only if in addition holdout net > 0 with p < 0.0125, > 0 with spreads +20%, > 0 with entry delayed one bar and fresh (2025-10..) net > 0.
Fewer than 150 holdout trades = INSUFFICIENT. Printed (no verdict): trades per week, cost per trade, net R per week, the year-by-year net. INTERPRETATION RULE: a gross edge on US2000 is a genuine out-of-sample market for the US-open idea (the US2000 is highly correlated with the other three, so it is a weaker replication than an independent market); Europe is independent of the US indices but trades on larger spreads (cost known from the 15-minute test: 0.11-0.15 R).
Run: venv/Scripts/python.exe scripts/research_rsi_open_replication_2026_11_27.py <dir with US2000_USD_M5.npz and the seven European *_M5.npz>
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
import research_rsi_opening_family_2026_11_27 as F  # noqa: E402

S, BH, E, V = F.S, F.BH, F.E, F.V
F.ALPHA = 0.05 / 4
CELLS = (("US2000", 70, 30), ("US2000", 75, 25), ("EUROPE", 70, 30), ("EUROPE", 75, 25))
US2000_SPEC = ("America/New_York", 570, 960, "US")


def yearly(rows):
    by = {}
    for r in rows:
        by.setdefault(r["date"][:4], []).append(r["R"])
    return "  ".join(f"{y}: {np.mean(v):+.2f} ({len(v)})" for y, v in sorted(by.items()))


def main(d):
    raws = {"US2000": {"US2000_USD": (V.load(d, "US2000_USD", "M5"), US2000_SPEC)} if os.path.exists(os.path.join(d, "US2000_USD_M5.npz")) else {},
            "EUROPE": {n: (V.load(d, n, "M5"), spec) for n, spec in S.SESSIONS.items() if spec[3] == "EUROPE" and os.path.exists(os.path.join(d, n + "_M5.npz"))}}
    print({k: sorted(v) for k, v in raws.items()}, flush=True)
    summary = {}
    for grp, hi, lo in CELLS:
        if not raws[grp]:
            print(f"\n=== {grp} {hi}/{lo}: no data -> NOT RUN ===")
            continue
        BH.set_lines(hi, lo)
        rows4 = tuple(F.trades_step(raws[grp], 300, f, dl) for f, dl in F.MODES)
        print(f"\n=== {grp} 5-minute bars, first 30 minutes, lines {hi}/{lo} ===", flush=True)
        summary[f"{grp} {hi}/{lo}"] = F.evaluate(f"{grp} M5 {hi}/{lo}", rows4, (0, 30), False)
        net = [r for r in rows4[0] if r["rel"] < 30]
        if net:
            print("     net by year: " + yearly(net))
            if grp == "EUROPE":
                print("     net by index: " + ", ".join(f"{n.split('_')[0]} {np.mean([r['R'] for r in net if r['pair'] == n]):+.2f}" for n in sorted({r['pair'] for r in net})))
    BH.set_lines(70, 30)
    print("\nSUMMARY (gross edge / profitable), K = 4:")
    for k, v in summary.items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main(sys.argv[1])
