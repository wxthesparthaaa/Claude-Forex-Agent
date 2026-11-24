"""Fetch the Fed H.4.1 weekly release pages (federalreserve.gov/releases/h41/YYYYMMDD/, 2013-2026; earlier archive pages are a stub) and save the Wednesday LEVELS of Table 1 items to a csv:
total factors supplying reserve funds (FS), reserve balances (RB), Treasury General Account (TGA), reverse repurchase agreements (RRP), all in USD millions.
Why not FRED: from this machine FRED resets the connection (2026-11-22). Release day is Thursday 16:30 ET (Friday etc. after a holiday); the Wednesday date is parsed from the page.
Usage: venv/Scripts/python.exe scripts/research_h41_fetch_2026_11_22.py OUT.csv
"""
import csv
import html as htmllib
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

import requests

H = {"User-Agent": "Mozilla/5.0"}
NUM = re.compile(r"^\(?-?[\d,]+\)?$")


def page(day):
    for off in (0, 1, 4):  # Thursday, then Friday or Monday (holiday shifts)
        d = day + timedelta(days=off)
        u = f"https://www.federalreserve.gov/releases/h41/{d.strftime('%Y%m%d')}/"
        for k in range(3):
            try:
                r = requests.get(u, headers=H, timeout=(8, 40))
                if r.status_code == 200 and "Reserve balances with Federal Reserve Banks" in r.text:
                    return d, r.text
                if r.status_code == 404:
                    break
            except Exception:  # noqa: BLE001
                time.sleep(2)
    return None, None


ROW = re.compile(r"^\s*(?:\(?\d{1,2}\)?\s+(?=\d{1,3},))?(-?[\d,]+)\s*(?:[+-]\s*)?([\d,]+)\s*(?:[+-]\s*)?([\d,]+)\s+(-?[\d,]+)(?!\d)")


def row_vals(txt, label):
    """First occurrence of the label followed by a Table-1 row: week average, change on a week, change on a year, Wednesday level (signs and a footnote number may sit in between)."""
    start = 0
    while True:
        i = txt.find(label, start)
        if i < 0:
            return None
        m = ROW.match(txt[i + len(label):i + len(label) + 120])
        if m and "," in m.group(1) and "," in m.group(4):
            return float(m.group(1).replace(",", "")), float(m.group(4).replace(",", ""))
        start = i + len(label)


def parse(day):
    d, html = page(day)
    if html is None:
        return day, None
    txt = re.sub(r"\s+", " ", htmllib.unescape(re.sub(r"<[^>]+>", " ", html)))
    wed = d - timedelta(days=((d.weekday() - 2) % 7) or 7)
    vals = {k: row_vals(txt, lab) for k, lab in (("FS", "Total factors supplying reserve funds"), ("RB", "Reserve balances with Federal Reserve Banks"),
                                                  ("TGA", "General Account"), ("RRP", "Reverse repurchase agreements"))}
    return day, (wed, d, vals)


def main(out):
    day, days = datetime(2013, 1, 3), []
    while day < datetime(2026, 11, 20):
        days.append(day)
        day += timedelta(days=7)
    rows, bad = [], []
    with ThreadPoolExecutor(6) as ex:
        for k, (dy, res) in enumerate(ex.map(parse, days)):
            if res is None:
                bad.append(dy.strftime("%Y-%m-%d"))
                continue
            wed, rel, v = res
            if any(x is None for x in v.values()):
                bad.append(dy.strftime("%Y-%m-%d") + " partial " + str({a: b for a, b in v.items() if b is None}))
                continue
            rows.append([wed.strftime("%Y-%m-%d"), rel.strftime("%Y-%m-%d")] + [v[x][1] for x in ("FS", "RB", "TGA", "RRP")] + [v[x][0] for x in ("FS", "RB", "TGA", "RRP")])
            if k % 100 == 0:
                print(k, rows[-1], flush=True)
    rows.sort()
    with open(out, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["wednesday", "released", "FS", "RB", "TGA", "RRP", "FS_avg", "RB_avg", "TGA_avg", "RRP_avg"])
        w.writerows(rows)
    print(len(rows), "weeks saved; missing/partial:", len(bad), bad[:15])


if __name__ == "__main__":
    main(sys.argv[1])
