"""
Research, 2026-10-25. PRE-REGISTERED before it was run, and written AFTER seeing that the position-book "stop magnet" signal
(P4: z(mag) >= 1.5 -> LONG, <= -1.5 -> SHORT) has a gross (mid, before costs) edge that is positive in every period in two separate
tests (P4: +0.064 / +0.032 / +0.052 R discovery / holdout / fresh; the cross-sectional CS2: +0.029 / +0.028 / +0.008) while its net
result is just below zero (P4 holdout -0.017). So this is a follow-up on an already-seen result, NOT a fresh discovery, and the
design below is built to stop me cherry-picking a threshold on the data that will judge it.

QUESTION. If the signal is real, a STRONGER signal should earn more per trade. The costs are about 0.05 R a trade, so only a
dose-response large enough to clear them matters.
SIGNAL: mag_d = (S_up_05 - L_dn_05)/(L+S) from the 12:00 UTC position book (shorts opened within 0.5% above price minus longs opened
within 0.5% below); z = causal z-score over the previous 250 snapshots (current excluded). LONG if z >= theta, SHORT if z <= -theta.
Same 16 instruments, entry at the 13:00 UTC H1 open (fill side), 48-bar exit, non-overlapping, R unit, 2%/yr financing as in the other book
scripts (rp.trades).
THRESHOLD CHOICE (fixed now, to prevent snooping): theta in {1.5, 2.0, 2.5, 3.0}. theta* = the value with the highest NET mean R in the
DISCOVERY period (< 2021-01-01) among those with at least 150 discovery trades. Holdout (2021-01..2025-09) and fresh (2025-10..) are
not used to choose anything. K = 1 (only theta* is judged), alpha 0.05.
Descriptive (no claim): the whole table by period for all four thresholds, with gross and net, so the dose-response shape is visible.
PRE-REGISTERED RULES for what the table must show for the signal to be called a real dose-response: gross mean R in the top threshold
bucket >= gross at theta 1.5 in discovery AND in holdout (monotone-ish), and net at theta* > 0 in holdout.
CREDIBLE (strict) only if ALL evaluator checks pass for theta*; PRACTICE-TRIAL flag as before.
Look-ahead self-test is shared with the positioning script (random positioning ~0, a peeking rule positive).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
import research_bond_lead_2026_10_24 as bl
import research_retail_positioning_2026_10_25 as rp

INSTRUMENTS = rp.INSTRUMENTS
THETAS = (1.5, 2.0, 2.5, 3.0)
bl.ALPHA = 0.05


def mag_signal(rows, theta):
    mag = {}
    for r in rows:
        tot = r["L"] + r["S"]
        if tot > 0 and r["L"] > 0 and r["S"] > 0:
            mag[r["date"]] = (r["S_up_05"] - r["L_dn_05"]) / tot
    out = {}
    for d, z in rp._z(mag).items():
        if z >= theta:
            out[d] = 1
        elif z <= -theta:
            out[d] = -1
    return out


def collect(data, sigs, theta, **kw):
    return [r for i in INSTRUMENTS for r in rp.trades(i, data[i], sigs[theta][i], **kw)]


def period_mean(rows, per):
    xs = [r["r"] for r in rows if bl._period(r["time"]) == per]
    return bl._m(xs), len(xs)


def main(book_dir, h1_dir):
    rp.selftest()
    data = {i: bl.load(h1_dir, i) for i in INSTRUMENTS}
    books = {i: rp.load_book(book_dir, i) for i in INSTRUMENTS}
    sigs = {th: {i: mag_signal(books[i], th) for i in INSTRUMENTS} for th in THETAS}
    print("\nDOSE-RESPONSE TABLE: mean R per trade (n) by period; NET is after spread and financing, GROSS is mid with no financing")
    table = {}
    for th in THETAS:
        net = collect(data, sigs, th)
        gross = [r for i in INSTRUMENTS for r in rp.trades(i, bl.mid_only(data[i]), sigs[th][i], fin=False)]
        table[th] = (net, gross)
        cells = []
        for per in ("discovery", "holdout", "fresh"):
            (mn, n), (mg, _) = period_mean(net, per), period_mean(gross, per)
            cells.append(f"{per[:4]} net {mn:+.4f} gross {mg:+.4f} (n={n})")
        print(f"  theta {th:3.1f}:  " + "  |  ".join(cells))
    eligible = [(period_mean(table[th][0], "discovery")[0], th) for th in THETAS if period_mean(table[th][0], "discovery")[1] >= 150]
    best_net, theta_star = max(eligible)
    print(f"\ntheta* chosen on DISCOVERY net only = {theta_star} (discovery net {best_net:+.4f})")
    g_top = {per: period_mean(table[THETAS[-1]][1], per)[0] for per in ("discovery", "holdout")}
    g_base = {per: period_mean(table[THETAS[0]][1], per)[0] for per in ("discovery", "holdout")}
    mono = all(g_top[p] >= g_base[p] for p in g_top)
    print(f"dose-response (gross at theta 3.0 >= gross at theta 1.5 in discovery AND holdout): {mono}  "
          f"(theta 3.0: {g_top}, theta 1.5: {g_base})")
    base = table[theta_star][0]
    stress = collect(data, sigs, theta_star, stress=True)
    delayed = collect(data, sigs, theta_star, delay=1)
    bl.evaluate(f"STOP MAGNET theta* = {theta_star}", base, stress, delayed, table[theta_star][1])
    rp.practice_flag(base, stress, delayed)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
