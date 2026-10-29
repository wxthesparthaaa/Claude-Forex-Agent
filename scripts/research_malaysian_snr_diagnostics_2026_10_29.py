"""POST-HOC diagnostics of the Malaysian SnR test (research_malaysian_snr_2026_10_29.py), written AFTER its result was seen (all variants about -0.19 R).
Not hypotheses; they only explain WHERE the loss comes from: (a) zero spread, no financing (is there any reaction at fresh levels before costs?);
(b) the same with target-first on bars that hit both (the optimistic bound of the stop-first convention); (c) a wider minimum stop (1 ATR) so the
spread is a smaller share of the risk; (d) how far price runs past the level within 48 hours vs how far it reverses (in ATR, mid prices).
Run: python3 scripts/research_malaysian_snr_diagnostics_2026_10_29.py /tmp/research/h1"""
import sys, os
import numpy as np
sys.path.insert(0, os.path.dirname(__file__))
import research_malaysian_snr_2026_10_29 as M


def zero_spread(d):
    out = dict(d)
    for k in "ohlc":
        out["bid" + k] = out["ask" + k] = d["mid" + k]
    return out


def main(h1_dir):
    base, mid, wide, exc = {}, {}, {}, []
    for inst in M.INSTRUMENTS:
        d = M.load(h1_dir, inst)
        lv = M.levels(d)
        z = zero_spread(d)
        base[inst] = (d, M.touches(d, lv), None)
        mid[inst] = (z, M.touches(z, lv), None)
        M.MIN_RISK_ATR = 1.0
        wide[inst] = (d, M.touches(d, lv), None)
        M.MIN_RISK_ATR = 0.25
        # (d) excursions after the first fresh touch, mid prices, in units of the trade's own risk
        for i, dr, lev, stop in M.touches(z, lv):
            seg = slice(i, min(i + 48, len(d["t"])))
            fav = (lev - d["midl"][seg].min()) if dr < 0 else (d["midh"][seg].max() - lev)
            adv = (d["midh"][seg].max() - lev) if dr < 0 else (lev - d["midl"][seg].min())
            exc.append((fav, adv, abs(stop - lev)))
    hold = lambda rows: M.m(M.per(rows, *M.HOLD))
    print(f"(a) zero spread, no financing, RR 2: holdout {hold(M.run(mid, 2.0, fin=False)):+.4f}; RR 1: {hold(M.run(mid, 1.0, fin=False)):+.4f}")
    print(f"    real spread, no financing, RR 2: {hold(M.run(base, 2.0, fin=False)):+.4f}")
    # (b) optimistic bound of the stop-first convention: count first exit bars that hit both stop and target
    rows = M.run(base, 2.0, fin=False)
    both = 0
    for inst, (d, tch, _) in base.items():
        for i, dr, lev, stop in tch:
            risk = abs(lev - stop); tgt = lev + dr * 2 * risk
            for k in range(i + 1, min(i + 49, len(d["t"]))):
                hs = d["bidl"][k] <= stop if dr > 0 else d["askh"][k] >= stop
                ht = d["bidh"][k] >= tgt if dr > 0 else d["askl"][k] <= tgt
                if hs or ht:
                    both += hs and ht
                    break
    print(f"(b) touches whose first exit bar hits BOTH stop and target: {both} of {sum(len(t) for _, t, _ in base.values())} "
          f"(max swing if all were wins instead: +{3 * both / max(1, len(rows)):.4f} R)")
    print(f"(c) minimum stop 1 ATR, real spread + financing, RR 2: holdout {hold(M.run(wide, 2.0)):+.4f}; RR 1: {hold(M.run(wide, 1.0)):+.4f}")
    e = np.array(exc)
    print(f"(d) within 48 h of a fresh touch: median reversal {np.median(e[:, 0] / e[:, 2]):.2f} x risk, median run THROUGH the level {np.median(e[:, 1] / e[:, 2]):.2f} x risk;"
          f" share that run through by more than the stop distance {np.mean(e[:, 1] > e[:, 2]):.0%}")


if __name__ == "__main__":
    main(sys.argv[1])
