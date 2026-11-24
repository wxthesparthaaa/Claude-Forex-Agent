"""
Research, 2026-11-24. PRE-REGISTERED before any result was seen. TEST #4 of the user's list: is the RSI breakout edge concentrated in particular hours of the US cash session? Same rule, data and engine as the
RSI breakout on SPX500 / NAS100 / US30 (RSI(14) crosses 70 / 30, next-bar entry at the real ask / bid, stop 1.5 ATR14, exit when RSI crosses back through 50 or at 16:00 New York; real bid/ask 2016-2026);
cells INDEX-M5 and INDEX-M15. Each trade is assigned to the New York time of its SIGNAL bar:
  OPEN 09:30-10:30, MORNING 10:30-12:00, MIDDAY 12:00-14:00, CLOSE 14:00-15:50.
K = 4 buckets x 2 cells = 8 tests, alpha 0.05 / 8 = 0.00625 (one-sided).
CONCENTRATION VERDICT per bucket and cell (fixed now): the bucket is a SWEET SPOT only if its HOLDOUT (2021-01..2025-09) net mean R exceeds the mean of the OTHER buckets' trades by a date-clustered t >= 2.5
(difference of means, standard errors added in quadrature, which corresponds to roughly p < 0.00625) AND the same difference is > 0 in discovery (2016-2020) AND the bucket's net is > 0 in the holdout. A bucket is a DEAD
ZONE if its holdout net is < 0 and below the others by t >= 2.5 with the same sign in discovery. If no bucket is either, the edge is broadly spread across the session and the rule is run all session.
Also printed: trades per week, gross and net by bucket and period, so a time window can be judged by its trade count as well as its edge.
Run: venv/Scripts/python.exe scripts/research_rsi_time_of_day_2026_11_24.py <dir with the US *_M5.npz>
"""
import os
import sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(__file__))
import research_rsi_breakout_2026_11_24 as RB  # noqa: E402
import research_rsi_breakout_followup_2026_11_24 as RF  # noqa: E402

E = RB.E
V = E.V
NY = ZoneInfo("America/New_York")
BUCKETS = (("OPEN 09:30-10:30", 570, 630), ("MORNING 10:30-12:00", 630, 720), ("MIDDAY 12:00-14:00", 720, 840), ("CLOSE 14:00-15:50", 840, 951))
disc, hold, fresh = ("2016-01-01", "2021-01-01"), ("2021-01-01", "2025-10-01"), ("2025-10-01", "2027-01-01")


def rows_for(raws, step, f=1.0):
    rows = []
    for name, raw in raws.items():
        P = E.prep(E.widen_raw(raw, f) if f != 1.0 else raw, "idx", step)
        t = P["t"]
        for r in RB.simulate(P, name, "B"):
            dt = datetime.fromtimestamp(int(t[r["sig"]]), timezone.utc).astimezone(NY)
            r["mod"] = dt.hour * 60 + dt.minute
            rows.append(r)
    return rows


def bucket_of(r):
    for label, a, b in BUCKETS:
        if a <= r["mod"] < b:
            return label
    return None


def main(us_dir):
    us5 = {p: V.load(us_dir, p, "M5") for p in E.INDICES if os.path.exists(os.path.join(us_dir, p + "_M5.npz"))}
    us15 = {n: RF.resample_period(raw, 900) for n, raw in us5.items()}
    for cname, (raws, step) in {"INDEX-M5": (us5, 300), "INDEX-M15": (us15, 900)}.items():
        net, gross = rows_for(raws, step), rows_for(raws, step, f=0.0)
        weeks = (datetime.fromisoformat(max(r["date"] for r in net)) - datetime.fromisoformat(min(r["date"] for r in net))).days / 7
        print(f"\n################ {cname}: {len(net)} trades ({len(net) / weeks:.1f}/wk) ################", flush=True)
        for label, a, b in BUCKETS:
            n_b = [r for r in net if a <= r["mod"] < b]
            g_b = [r for r in gross if a <= r["mod"] < b]
            o_b = [r for r in net if not (a <= r["mod"] < b)]
            line = []
            ok_sweet = ok_dead = True
            for plab, per in (("disc", disc), ("hold", hold), ("fresh", fresh)):
                mb, sb, nb = E.stats(E.win(n_b, *per))
                mo, so, _ = E.stats(E.win(o_b, *per))
                gb = E.stats(E.win(g_b, *per))[0]
                d, se = mb - mo, (sb ** 2 + so ** 2) ** 0.5
                line.append(f"{plab}: n={nb} net {mb:+.3f} gross {gb:+.3f} (vs others {d:+.3f}, t {d / se:+.2f})")
                if plab == "disc":
                    ok_sweet = ok_sweet and d > 0
                    ok_dead = ok_dead and d < 0
                if plab == "hold":
                    ok_sweet = ok_sweet and d / se >= 2.5 and mb > 0
                    ok_dead = ok_dead and d / se <= -2.5 and mb < 0
            verdict = "SWEET SPOT" if ok_sweet else ("DEAD ZONE" if ok_dead else "no concentration")
            print(f"  {label:20s} {len(n_b) / weeks:5.1f}/wk | " + " | ".join(line) + f" -> {verdict}")


if __name__ == "__main__":
    main(sys.argv[1])
