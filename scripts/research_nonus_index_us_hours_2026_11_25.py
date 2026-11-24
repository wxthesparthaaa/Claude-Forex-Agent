"""
Research, 2026-11-25 (daily routine), second family of the day. PRE-REGISTERED before any result was seen; committed before the first run.
DISCLOSURE: chosen AFTER seeing today's spillover results (research_index_momentum_spillover_2026_11_25.py), in which JP225 had the largest gross
response to the US signal; so this is a post-hoc-motivated family and its verdict should be read with that in mind.

HYPOTHESIS (family O, own-market momentum on NON-US index CFDs during the US CASH SESSION): the non-US indices showed a gross momentum edge in their
own local sessions (+0.069 R, 2026-11-24) that their local-session spreads ate. During US hours the price of these CFDs is set by the globally
traded index futures, driven by the same US-session flow that carries the US momentum, and (hypothesis) the cost relative to the move is lower.
RULE: exactly the RSI breakout of src/rsi_breakout_addon.py on M15 (own RSI(14) cross of 70 / 30, bar starting 09:30-15:30 New York, next-bar entry at
real ask/bid, stop 1.5 x own ATR14, exit when own RSI crosses back through 50 or at the close of the bar starting 15:45 New York or before a gap).
CELL: O-IDX = JP225_USD, DE30_EUR, UK100_GBP, EU50_EUR pooled. K = 1 (this family), alpha 0.05; day's running variant count 3 + 1 = 4.
DECISION RULE: same as the spillover script (shipping bar and practice-trial flag; RULED OUT / WEAK otherwise); self-tests the same (random walk with
15-minute bars built from a 120-step sub-path; peeking rule).
Run: python3 scripts/research_nonus_index_us_hours_2026_11_25.py /tmp/research/m15
"""
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))
import research_index_momentum_spillover_2026_11_25 as M  # noqa: E402

M.ALPHA = 0.05
T = M.S_IDX


def run_cell(name, D, targets, kind, **kw):
    rows = []
    for tg in targets:
        sig = M.crosses(D[tg], M.S_WIN)
        rows += [dict(r, inst=tg) for r in M.simulate(D[tg], sig, D[tg], 945, **kw)]
    return rows


if __name__ == "__main__":
    d = sys.argv[1]
    D = {nm: M.load(d, nm) for nm in T}
    W = {nm: M.load(d, nm, 1.2) for nm in T}
    M.run_cell = run_cell
    import numpy as np  # noqa: F401
    orig = M.selftest

    def st(D):
        rng_cells = (("O-IDX", T, "O"),)
        for cell, tgs, kind in rng_cells:
            E = dict(D)
            rng = M.np.random.default_rng(11)
            for tg in tgs:
                x = D[tg]
                sd = M.np.nanstd(M.np.diff(M.np.log(x["mid"]["c"])))
                n, sub = len(x["t"]), 120
                fine = x["mid"]["c"][0] * M.np.exp(M.np.cumsum(rng.normal(0, sd / M.math.sqrt(sub), n * sub))).reshape(n, sub)
                path = fine[:, -1]
                o = M.np.concatenate([[x["mid"]["c"][0]], path[:-1]])
                y = {"t": x["t"], "vol": x["vol"]}
                for k, v in (("o", o), ("h", M.np.maximum(fine.max(axis=1), o)), ("l", M.np.minimum(fine.min(axis=1), o)), ("c", path)):
                    hs = (x["ask_" + k] - x["bid_" + k]) / 2
                    y["bid_" + k], y["ask_" + k] = v - hs, v + hs
                E[tg] = M.prep(y)
            n, m, se = M.stats(run_cell(cell, E, tgs, kind, gross=True))
            pk = M.stats(run_cell(cell, D, tgs, kind, gross=True, peek=True))
            print(f"SELFTEST {cell}: random-walk gross {m:+.4f} (se {se:.4f}, n {n}); peek {pk[1]:+.3f} -> "
                  f"{'OK' if abs(m) <= 3.5 * se + 0.02 and pk[1] > 0.3 else 'FAIL'}", flush=True)
    st(D)
    M.report("O-IDX", run_cell("O", D, T, "O"), run_cell("O", D, T, "O", gross=True), run_cell("O", W, T, "O"), run_cell("O", D, T, "O", delay=1))
