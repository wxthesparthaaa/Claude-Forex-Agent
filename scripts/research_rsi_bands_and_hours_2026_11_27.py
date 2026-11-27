"""
Research, 2026-11-27. PRE-REGISTERED before any result was seen. Two user questions about the RSI breakout (RSI(14) crosses above the upper line -> LONG, below the lower line -> SHORT; entry at the NEXT bar's open at
the real ask / bid; hard stop 1.5 x ATR14 from the fill; exit when RSI crosses back through 50 against the trade; R = result / stop distance; real OANDA bid/ask 2016-2026; one position per instrument):

 Q1. FOREX, MORE LENIENT OR STRICTER RSI LINES: would any pair of lines show an edge? Already known (2026-11-24): the 70/30 lines are null on forex (FX-M15 gross -0.019; FX-H1 gross +0.052, holdout +0.011).
     PART A tests the other lines on the nine forex pairs of the earlier study (EUR_USD, GBP_USD, USD_JPY, AUD_USD, USD_CAD, USD_CHF, NZD_USD, EUR_JPY, GBP_JPY), 15-minute bars (FX-M15) and 1-hour bars (FX-H1):
       lines (upper/lower): 60/40, 65/35, 75/25, 80/20, 85/15.  70/30 is printed as the reference, not as a test.  5 line pairs x 2 cells = 10 tests, alpha 0.05 / 10 = 0.005 (one-sided).
     The exit line (50) and the 1.5 ATR stop do NOT change; only the entry lines do.
     VERDICT per test (fixed now): GROSS EDGE only if the pooled gross mean R (zero spread, trade-weighted, date-clustered) is > 0 with p < 0.005 AND > 0 in BOTH discovery (2016-2020) and holdout (2021-01..2025-09)
     AND > 0 for longs and shorts separately. PROFITABLE only if, in addition, holdout NET mean R > 0 with p < 0.005, > 0 with spreads +20%, > 0 with entry delayed one bar, and the fresh period (2025-10..) net > 0.
     Fewer than 150 holdout trades = INSUFFICIENT, no verdict. Also printed (no verdict): trades a week and whether gross rises or falls steadily from the lenient to the strict lines (a smooth gradient is more believable
     than a single lucky pair of lines).

 Q2. US INDEXES, IS THE EDGE ONLY AT THE OPEN, OR ARE THERE OTHER GOOD TIMES? Already known (2026-11-24): four buckets (open hour, morning, midday, close) -> none qualified as a sweet spot or dead zone; the open hour was
     the most consistent (M5 net disc +0.030 / hold +0.111 / fresh +0.232) while midday and close flipped sign between periods; the overnight session 18:30-09:15 NY (2026-11-25) was ruled out (net -0.039, gross +0.038).
     PART B repeats the time-of-day cut with FINER buckets on SPX500 / NAS100 / US30 5-minute bars (cash session, signal bar's New York start time):
       09:30-10:00 (first 6 bars), 10:00-11:00, 11:00-12:00, 12:00-13:00, 13:00-14:00, 14:00-15:00, 15:00-15:50  (7 buckets)
     plus ONE extra test: EX-OPEN = every trade whose signal bar starts at 10:00 New York or later (does the rule work at all once the first half hour is removed?).  K = 8, alpha 0.05 / 8 = 0.00625 (one-sided).
     SWEET SPOT per bucket (fixed now): the bucket's HOLDOUT net mean R exceeds the mean of all OTHER trades by a date-clustered t >= 2.5 (difference of means, standard errors added in quadrature) AND the same difference
     is > 0 in discovery AND the bucket's holdout net is > 0 AND on the 15-minute replication (same buckets, bars resampled from the same data; a bucket there = signal bar start inside the same window) the holdout net is > 0.
     DEAD ZONE: the mirror image (holdout net < 0 and below the others by t <= -2.5, same sign in discovery).  EX-OPEN verdict: GROSS EDGE / PROFITABLE with the same rule as Q1 (alpha 0.00625, long and short both > 0).
     Also printed (no verdict): trades a week, share of all trades, gross and net by period, for every bucket, so the value of a time window can be judged by its trade count as well as its edge.

SELF-TEST (run first): a random walk at zero spread gives ~0 (within 3.5 se + 0.02) for the lenient (60/40) and the strict (85/15) lines, and the "peeking" version (direction from the close 8 bars ahead) is clearly positive.
DISCLOSURE: before this script the 70/30 / RSI 10-14-21 x 65-75 gradient on INDEXES (all positive, more extreme larger) and the four-bucket result above were already known; the FX lines other than 70/30 and the finer
hourly buckets had not been looked at.
Run: venv/Scripts/python.exe scripts/research_rsi_bands_and_hours_2026_11_27.py <dir with the *_M15.npz (nine FX pairs) and the US *_M5.npz>
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
FX9 = ["EUR_USD", "GBP_USD", "USD_JPY", "AUD_USD", "USD_CAD", "USD_CHF", "NZD_USD", "EUR_JPY", "GBP_JPY"]
LINES = [(60, 40), (65, 35), (70, 30), (75, 25), (80, 20), (85, 15)]
ALPHA_A, ALPHA_B = 0.05 / 10, 0.05 / 8
disc, hold, fresh = ("2016-01-01", "2021-01-01"), ("2021-01-01", "2025-10-01"), ("2025-10-01", "2027-01-01")
BUCKETS = (("09:30-10:00", 570, 600), ("10:00-11:00", 600, 660), ("11:00-12:00", 660, 720), ("12:00-13:00", 720, 780),
           ("13:00-14:00", 780, 840), ("14:00-15:00", 840, 900), ("15:00-15:50", 900, 951))


def set_lines(hi, lo):
    RB.PARAMS["hi"], RB.PARAMS["lo"], RB.PARAMS["mid"] = hi, lo, 50
    RB.PARAMS.pop("confirm", None)


def run(raws, kind, step, f=1.0, delay=0, tag="B"):
    rows = []
    for name, raw in raws.items():
        P = E.prep(E.widen_raw(raw, f) if f != 1.0 else raw, kind, step)
        t = P["t"]
        for r in RB.simulate(P, name, "B", delay=delay):
            dt = datetime.fromtimestamp(int(t[r["sig"]]), timezone.utc).astimezone(NY)
            r["mod"] = dt.hour * 60 + dt.minute
            rows.append(r)
    return rows


def weeks_of(rows):
    return (datetime.fromisoformat(max(r["date"] for r in rows)) - datetime.fromisoformat(min(r["date"] for r in rows))).days / 7


def verdict(net, gross, stress, delayed, alpha, label):
    """Prints the standard block and returns (gross_edge, profitable)."""
    gm, gse, _ = E.stats(gross)
    ml, ms = E.stats([r for r in gross if r["dir"] == 1])[0], E.stats([r for r in gross if r["dir"] == -1])[0]
    wk = weeks_of(net) if net else 1
    print(f"  {label}: trades {len(net)} ({len(net) / wk:.1f}/wk) | POOLED GROSS {gm:+.3f} (se {gse:.3f}, p {E.pval(gm, gse):.4f}) NET {E.stats(net)[0]:+.3f} | gross long {ml:+.3f} short {ms:+.3f}", flush=True)
    ok = gm > 0 and E.pval(gm, gse) < alpha and ml > 0 and ms > 0
    prof, insufficient = True, False
    for lab, per in (("DISCOVERY 2016-2020", disc), ("HOLDOUT 2021-01..2025-09", hold), ("FRESH 2025-10..", fresh)):
        sn, sg, ss, sd = E.win(net, *per), E.win(gross, *per), E.win(stress, *per), E.win(delayed, *per)
        m, se, n = E.stats(sn)
        mg = E.stats(sg)[0]
        print(f"     {lab:26s} n={n:5d} net {m:+.3f} (se {se:.3f}, p {E.pval(m, se):.4f})  gross {mg:+.3f}  +20% {E.stats(ss)[0]:+.3f}  delay {E.stats(sd)[0]:+.3f}  win {sum(1 for r in sn if r['R'] > 0) / max(n, 1):4.0%}")
        if lab.startswith("DISC") or lab.startswith("HOLD"):
            ok = ok and mg > 0
        if lab.startswith("HOLD"):
            insufficient = n < 150
            prof = prof and m > 0 and E.pval(m, se) < alpha and E.stats(ss)[0] > 0 and E.stats(sd)[0] > 0
        if lab.startswith("FRESH"):
            prof = prof and m > 0
    if insufficient:
        print("     -> INSUFFICIENT (< 150 holdout trades), no verdict")
        return None, None
    print(f"     -> GROSS-EDGE VERDICT: {'YES' if ok else 'NO'} | PROFITABLE: {'YES' if (ok and prof) else 'NO'}")
    return ok, ok and prof


def part_a(dirpath):
    fx15 = {p: V.load(dirpath, p, "M15") for p in FX9 if os.path.exists(os.path.join(dirpath, p + "_M15.npz"))}
    print(f"\n######## PART A: forex RSI lines, pairs {sorted(fx15)} ########", flush=True)
    cells = {"FX-M15": (fx15, 900), "FX-H1": ({p: E.resample_h1(v) for p, v in fx15.items()}, 3600)}
    out = {}
    for cname, (raws, step) in cells.items():
        for hi, lo in LINES:
            set_lines(hi, lo)
            print(f"\n=== {cname} lines {hi}/{lo}{'  (REFERENCE, not a test)' if (hi, lo) == (70, 30) else ''} ===", flush=True)
            net, gross, stress, delayed = (run(raws, "fx", step, f, d) for f, d in ((1.0, 0), (0.0, 0), (1.2, 0), (1.0, 1)))
            out[(cname, hi)] = verdict(net, gross, stress, delayed, ALPHA_A, f"{cname} {hi}/{lo}") + (E.stats(gross)[0], len(net) / weeks_of(net))
    set_lines(70, 30)
    print("\nGRADIENT (pooled gross R, trades/week), lenient -> strict:")
    for cname in cells:
        print("  " + cname + ": " + "  ".join(f"{hi}/{lo}: {out[(cname, hi)][2]:+.3f} ({out[(cname, hi)][3]:.1f}/wk)" for hi, lo in LINES))
    print("SUMMARY (gross edge / profitable):", {f"{c} {h}/{100 - h}": out[(c, h)][:2] for (c, h) in out if h != 70})


def part_b(dirpath):
    set_lines(70, 30)
    us5 = {p: V.load(dirpath, p, "M5") for p in ("SPX500_USD", "NAS100_USD", "US30_USD") if os.path.exists(os.path.join(dirpath, p + "_M5.npz"))}
    us15 = {n: RF.resample_period(raw, 900) for n, raw in us5.items()}
    print(f"\n######## PART B: US index hours, {sorted(us5)} ########", flush=True)
    res = {}
    for cname, (raws, step) in {"INDEX-M5": (us5, 300), "INDEX-M15": (us15, 900)}.items():
        net, gross, stress, delayed = (run(raws, "idx", step, f, d) for f, d in ((1.0, 0), (0.0, 0), (1.2, 0), (1.0, 1)))
        wk = weeks_of(net)
        print(f"\n=== {cname}: {len(net)} trades ({len(net) / wk:.1f}/wk) ===", flush=True)
        res[cname] = (net, gross, stress, delayed, wk)
        for label, a, b in BUCKETS:
            n_b = [r for r in net if a <= r["mod"] < b]
            g_b = [r for r in gross if a <= r["mod"] < b]
            o_b = [r for r in net if not (a <= r["mod"] < b)]
            line, sweet, dead = [], True, True
            for plab, per in (("disc", disc), ("hold", hold), ("fresh", fresh)):
                mb, sb, nb = E.stats(E.win(n_b, *per))
                mo, so, _ = E.stats(E.win(o_b, *per))
                gb = E.stats(E.win(g_b, *per))[0]
                d, se = mb - mo, (sb ** 2 + so ** 2) ** 0.5 or 1.0
                line.append(f"{plab}: n={nb} net {mb:+.3f} gross {gb:+.3f} (vs others {d:+.3f}, t {d / se:+.2f})")
                if plab == "disc":
                    sweet, dead = sweet and d > 0, dead and d < 0
                if plab == "hold":
                    sweet = sweet and d / se >= 2.5 and mb > 0
                    dead = dead and d / se <= -2.5 and mb < 0
            res[cname] += ((label, sweet, dead),)
            print(f"  {label:12s} {len(n_b) / wk:5.1f}/wk ({len(n_b) / len(net):4.0%} of trades) | " + " | ".join(line))
    print("\nBUCKET VERDICTS (sweet spot needs M5 sweet AND M15 holdout net > 0):")
    net15 = res["INDEX-M15"][0]
    for i, (label, a, b) in enumerate(BUCKETS):
        _, sweet, dead = res["INDEX-M5"][5 + i]
        m15 = E.stats(E.win([r for r in net15 if a <= r["mod"] < b], *hold))[0]
        print(f"  {label:12s} M5 sweet {sweet} dead {dead} | M15 holdout net {m15:+.3f} -> {'SWEET SPOT' if (sweet and m15 > 0) else ('DEAD ZONE' if dead else 'no concentration')}")
    for cname in ("INDEX-M5", "INDEX-M15"):
        net, gross, stress, delayed, wk = res[cname][:5]
        sel = lambda rows: [r for r in rows if r["mod"] >= 600]
        print(f"\n=== EX-OPEN (signal bar from 10:00 New York) on {cname} ===")
        verdict(sel(net), sel(gross), sel(stress), sel(delayed), ALPHA_B, f"{cname} EX-OPEN")
        op = lambda rows: [r for r in rows if r["mod"] < 600]
        print(f"=== first half hour only (09:30-10:00) on {cname}, for comparison ===")
        verdict(op(net), op(gross), op(stress), op(delayed), ALPHA_B, f"{cname} OPEN-30MIN")


def selftest(dirpath):
    for hi, lo in ((60, 40), (85, 15)):
        set_lines(hi, lo)
        RB.selftest(dirpath)
    set_lines(70, 30)


def main(dirpath):
    selftest(dirpath)
    part_a(dirpath)
    part_b(dirpath)


if __name__ == "__main__":
    main(sys.argv[1])
