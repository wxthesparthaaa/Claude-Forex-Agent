"""Quick check of a pasted TradingView idea (2026-11-21): "VIX above 30 and RSI above 70 (on the VIX) = macro washout, buy".

Reading used: RSI(14, Wilder) computed on the VIX close; signal day = VIX close > 30 and VIX RSI > 70.
Forward S&P 500 returns (close to close) after the signal vs every day, plus episode-level results
(signals within 10 trading days of each other are one episode). Data: Yahoo ^VIX and ^GSPC daily.
Not a trading strategy test (no costs, no stops): it only asks whether the signal has any forward edge.
"""
import json, math, statistics, urllib.request
from datetime import datetime, timezone


def fetch(sym):
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?period1=631152000&period2=1795000000&interval=1d"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    d = json.load(urllib.request.urlopen(req, timeout=60))["chart"]["result"][0]
    ts, cl = d["timestamp"], d["indicators"]["quote"][0]["close"]
    return {datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%d"): c for t, c in zip(ts, cl) if c is not None}


vix, spx = fetch("%5EVIX"), fetch("%5EGSPC")
dates = sorted(set(vix) & set(spx))
v = [vix[d] for d in dates]
s = [spx[d] for d in dates]
print("days", len(dates), dates[0], "->", dates[-1])

# Wilder RSI(14) on VIX
rsi = [None] * len(v)
gain = loss = 0.0
for i in range(1, len(v)):
    ch = v[i] - v[i - 1]
    g, l = max(ch, 0.0), max(-ch, 0.0)
    if i <= 14:
        gain += g / 14; loss += l / 14
        if i == 14:
            rsi[i] = 100 - 100 / (1 + gain / loss) if loss else 100
    else:
        gain = (gain * 13 + g) / 14; loss = (loss * 13 + l) / 14
        rsi[i] = 100 - 100 / (1 + gain / loss) if loss else 100

sig = [i for i in range(15, len(v)) if v[i] > 30 and rsi[i] is not None and rsi[i] > 70]
only_vix = [i for i in range(15, len(v)) if v[i] > 30]
print(f"VIX>30 days: {len(only_vix)} ({len(only_vix)/len(v):.1%}); VIX>30 & RSI>70 days: {len(sig)}")


def fwd(i, h):
    return s[i + h] / s[i] - 1 if i + h < len(s) else None


def summary(idx, label):
    for h in (1, 5, 10, 20):
        r = [fwd(i, h) for i in idx if fwd(i, h) is not None]
        if len(r) < 3:
            continue
        print(f"  {label:>22s} h={h:2d}d n={len(r):4d} mean {statistics.fmean(r)*100:+.2f}% median {statistics.median(r)*100:+.2f}% hit {sum(1 for x in r if x>0)/len(r):.0%}")


allidx = list(range(15, len(v)))
summary(allidx, "all days")
summary(only_vix, "VIX>30")
summary(sig, "VIX>30 & RSI>70")

# episodes
eps, last = [], -99
for i in sig:
    if i - last > 10:
        eps.append(i)
    last = i
print(f"\nEPISODES (first signal day after a 10-day gap): {len(eps)}")
for i in eps:
    print(f"  {dates[i]} VIX {v[i]:5.1f} RSI {rsi[i]:4.0f} | S&P next 5d {fwd(i,5)*100 if fwd(i,5) is not None else float('nan'):+5.1f}%  10d {fwd(i,10)*100 if fwd(i,10) is not None else float('nan'):+5.1f}%  20d {fwd(i,20)*100 if fwd(i,20) is not None else float('nan'):+5.1f}%  | worst close in next 20d {min(s[i+k]/s[i]-1 for k in range(1, min(21, len(s)-i)))*100:+5.1f}%")
for h in (5, 10, 20):
    r = [fwd(i, h) for i in eps if fwd(i, h) is not None]
    if len(r) > 2:
        se = statistics.pstdev(r) / math.sqrt(len(r))
        print(f"  episode mean {h}d: {statistics.fmean(r)*100:+.2f}% (se {se*100:.2f}, t {statistics.fmean(r)/se:+.2f}, n={len(r)}); unconditional {statistics.fmean([fwd(i,h) for i in allidx if fwd(i,h) is not None])*100:+.2f}%")
