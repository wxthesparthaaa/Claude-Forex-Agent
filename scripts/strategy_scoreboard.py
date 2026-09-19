"""
Evidence scoreboard: one row per strategy (journal experiment_tag) showing
live-demo results against the bar in EVIDENCE_BAR.md. Reads the journal
from the state-sync branch by default (the live account's record), or a
local path via --journal.

  ./venv/Scripts/python.exe scripts/strategy_scoreboard.py
"""
from __future__ import annotations

import argparse
import json
import math
import subprocess
from collections import defaultdict

MIN_LIVE_TRADES = 100


def load_journal(path):
    if path:
        return json.load(open(path, encoding="utf-8"))
    subprocess.run(["git", "fetch", "origin", "state-sync", "--quiet"], check=False)
    raw = subprocess.run(["git", "show", "origin/state-sync:config/trade_journal.json"],
                         capture_output=True, text=True, check=True, encoding="utf-8").stdout
    return json.loads(raw)


def summarize(entries):
    closed = [e for e in entries if e.get("status") in ("SUCCESSFUL", "FAILED") and e.get("risk_amount")]
    n = len(closed)
    if n == 0:
        return None
    r = [e["realized_pnl"] / e["risk_amount"] for e in closed]
    mean = sum(r) / n
    sd = math.sqrt(sum((x - mean) ** 2 for x in r) / (n - 1)) if n > 1 else 0.0
    low = mean - 1.96 * sd / math.sqrt(n) if n > 1 else float("-inf")
    wins = sum(e["status"] == "SUCCESSFUL" for e in closed)
    pnl = sum(e["realized_pnl"] for e in closed)
    passed = n >= MIN_LIVE_TRADES and low > 0 and pnl > 0
    return {"n": n, "win": 100 * wins / n, "mean_r": mean, "r_low95": low, "pnl": pnl, "pass": passed}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--journal", help="local trade_journal.json (default: state-sync branch)")
    args = ap.parse_args()
    by_tag = defaultdict(list)
    for e in load_journal(args.journal):
        by_tag[e.get("experiment_tag") or "BASE / untagged"].append(e)
    print(f"{'strategy':22s} {'closed':>6s} {'win%':>6s} {'meanR':>7s} {'R 95% low':>9s} {'net P&L':>10s}  live bar (n>={MIN_LIVE_TRADES}, R low>0)")
    for tag, entries in sorted(by_tag.items(), key=lambda kv: -len(kv[1])):
        s = summarize(entries)
        if s is None:
            continue
        print(f"{tag:22s} {s['n']:6d} {s['win']:6.1f} {s['mean_r']:+7.2f} {s['r_low95']:+9.2f} {s['pnl']:+10.2f}  "
              f"{'PASS' if s['pass'] else 'not yet'}")
    print("\nA strategy also needs a CLEAN backtest (invalid entries dropped) agreeing with live -- see EVIDENCE_BAR.md.")


if __name__ == "__main__":
    main()
