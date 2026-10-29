"""POST-HOC check of research_social_media_round_2026_10_29.py, written AFTER its results: how much do the engine's conservative conventions (stop first when a
bar hits both stop and target; the fill bar can only stop out) cost each family on pure random-walk prices with zero spread? A large-sample version of the
pre-registered self-test (8 x 200,000 bars per family). Subtracting this from each family's zero-spread holdout gives its before-cost edge versus chance.
Run: python3 scripts/research_social_media_engine_bias_2026_10_29.py"""
import math
import os
import sys
from datetime import datetime, timezone

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
import research_social_media_round_2026_10_29 as S

for name, (tf, fn) in S.FAMILIES.items():
    rs = []
    for seed in range(8):
        rnd = np.random.default_rng(100 + seed)
        n, step, vol = 200000, (900 if tf == "m15" else 3600), (0.001 if tf == "m15" else 0.002)
        t = datetime(2010, 1, 4, tzinfo=timezone.utc).timestamp() + step * np.arange(n)
        c = 100 * np.exp(np.cumsum(rnd.normal(0, vol, n)))
        o = np.r_[100.0, c[:-1]]
        h = np.maximum(o, c) + np.abs(rnd.normal(0, vol / 2, n)) * c
        l = np.minimum(o, c) - np.abs(rnd.normal(0, vol / 2, n)) * c
        d = {"t": t}
        for s in ("mid", "bid", "ask"):
            d[s + "o"], d[s + "h"], d[s + "l"], d[s + "c"] = o, h, l, c
        rs += [r["r"] for r in S.simulate(d, fn(d), fin=False)]
    print(f"{name}: random-walk engine bias {np.mean(rs):+.4f} (se {np.std(rs) / math.sqrt(len(rs)):.4f}, n {len(rs)})", flush=True)
