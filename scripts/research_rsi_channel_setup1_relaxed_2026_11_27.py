"""
Research, 2026-11-27. PRE-REGISTERED before any of its results was seen. FOLLOW-UP to research_rsi_channel_setups_2026_11_27.py. There SET-UP 1 (inside the parallel lines: price touched the lower line in the last 6 bars within 0.5 ATR and RSI crosses above 65; short mirrored with 35) had a small positive gross in both directions (65/35: gross +0.049 R, 282 trades,
0.5 a week; 70/30: gross +0.061, 113 trades) but too few trades for a verdict (holdout 135 and 52 trades, minimum 150). This script loosens ONLY the line-touch rule to raise the number of trades and asks whether the small positive gross survives with a sample large enough to judge:
touch tolerance {1.0 ATR, 1.5 ATR} x touch window {6 bars, 12 bars} x RSI lines {65/35, 70/30}, exit A (fixed 1.5 : 1 take-profit plus the RSI exit) = 8 cells, K = 8, alpha 0.05 / 8 = 0.00625. Everything else exactly as in the earlier script (H4 lines / 30-minute entries, stop beyond the line and the 12-bar extreme, 96-bar time stop, nine forex pairs, real bid / ask).
VERDICT per cell and the random-walk benchmark rule: identical to the earlier script (gross edge needs p < 0.00625, discovery and holdout > 0, longs and shorts > 0, and the gross above the zero-spread random-walk benchmark by >= 2 combined SE; profitable needs holdout net > 0 with p < 0.00625, +20% spread > 0, one-bar delay > 0, fresh net > 0; fewer than 150 holdout trades = INSUFFICIENT).
DISCLOSURE: the direction of the follow-up (loosening the touch) was chosen after seeing that the strict cells were positive but thin; the earlier 12 cells are not part of this K.
Run: venv/Scripts/python.exe scripts/research_rsi_channel_setup1_relaxed_2026_11_27.py <dir with the nine *_M15.npz>
"""
import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
import research_rsi_channel_setups_2026_11_27 as R  # noqa: E402

E, PC, V = R.E, R.PC, R.V
ALPHA = 0.05 / 8
GRID = [(tol, win, ln) for tol in (1.0, 1.5) for win in (6, 12) for ln in ((65, 35), (70, 30))]


def set_touch(tol, win):
    R.TOUCH_ATR, R.TOUCH_BARS = tol, win


def benchmark(raw, tol, win, ln):
    mid = (raw["bid_c"] + raw["ask_c"]) / 2
    dl = np.diff(np.log(mid))
    sig = float(1.4826 * np.median(np.abs(dl - np.median(dl))))
    rows = []
    set_touch(tol, win)
    for seed in (1501, 1502, 1503):
        rg = np.random.default_rng(seed)
        n = len(raw["t"])
        K = 40
        steps = rg.normal(0, sig / math.sqrt(K), (n, K))
        path = 1.1 * np.exp(np.cumsum(steps.reshape(-1)).reshape(n, K))
        cl = path[:, -1]
        op = np.concatenate([[1.1], cl[:-1]])
        syn = {"t": raw["t"], "vol": raw["vol"]}
        for side in ("bid", "ask"):
            syn[f"{side}_o"], syn[f"{side}_c"], syn[f"{side}_h"], syn[f"{side}_l"] = op, cl, path.max(axis=1), path.min(axis=1)
        S = R.make_ctx(syn)
        rows += R.simulate(S, R.signals(S, 1, *ln), 1, ln[0], ln[1], "A", S["raw"])
        del path, steps
    return E.stats(rows)


def main(dirpath):
    raws = {p: V.load(dirpath, p, "M15") for p in PC.FX9 if os.path.exists(os.path.join(dirpath, p + "_M15.npz"))}
    ctxs = {p: R.make_ctx(r) for p, r in raws.items()}
    summary = {}
    for tol, win, ln in GRID:
        set_touch(tol, win)
        label = f"SET-UP 1 relaxed / touch {tol} ATR in {win} bars / RSI {ln[0]}/{ln[1]} / exit A"
        print(f"\n=== {label} ===", flush=True)
        cell = {mode: R.cell_rows(ctxs, 1, ln, "A", f, dl) for mode, f, dl in PC.MODES}
        v = PC.report(label, cell, alpha=ALPHA)
        if len(cell["gross"]) > 3:
            gm, gse, _ = E.stats(cell["gross"])
            bm, bse, bn = benchmark(raws["EUR_USD"], tol, win, ln)
            set_touch(tol, win)
            z = (gm - bm) / math.sqrt(gse ** 2 + bse ** 2) if bse == bse else float("nan")
            net = cell["net"]
            med = sorted(R.pips(r["pair"], r["risk"]) for r in net)[len(net) // 2]
            print(f"     random-walk benchmark {bm:+.3f} (se {bse:.3f}, n {bn}); gross excess z {z:+.2f} | cost per trade {gm - E.stats(net)[0]:+.3f} R | net R/week {sum(r['R'] for r in net) / PC.weeks_of(net):+.2f} | median stop {med:.0f} pips | gross pips per trade about {gm * med:+.1f} vs cost about {(gm - E.stats(net)[0]) * med:.1f}")
            if v and v[0] and not (z >= 2.0):
                v = (False, False)
        summary[label] = v
    print("\nSUMMARY (gross edge incl. the benchmark / profitable), K = 8:")
    for k, v in summary.items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main(sys.argv[1])
