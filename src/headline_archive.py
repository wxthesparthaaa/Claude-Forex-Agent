"""Daily headline archive (started 2026-11-26): READ-ONLY news collection for later research. It never touches trading, orders or the dashboard toggles.

WHY: the 2026-11-26 headline-tone backtest (RESEARCH_INDEX.md) could only use a year of keyword-scored Finnhub headlines and ended with 71 called days (underpowered), because
free headline history is thin and patchy. Archiving the headlines every hour from now on grows an honest, point-in-time record (every item keeps the timestamp the source
published it) that a later, better test can use without any hindsight.

WHAT IT STORES (compact: epoch seconds, headline cut to 160 characters, source): Finnhub general market news, Finnhub company news for SPY and QQQ (today and yesterday, UTC),
and the public RSS feeds of Yahoo Finance (^DJI, ^GSPC, ^IXIC), MarketWatch (Dow Jones top stories) and Investing.com news. Items are de-duplicated on the first 60 lowercase
characters of the headline. Each source is fetched independently; one failing never stops the others, and the whole job never raises (it runs inside the scheduler beside the trading jobs).

WHERE: `config/headline_archive.json` (a rolling window of the last ROLLING_DAYS days, synced to GitHub like every state file so it survives Render restarts) plus one write-once file
per COMPLETED UTC day, `config/headlines/YYYY-MM-DD.json`, pushed to GitHub with push_binary_file (re-pushed only if late items change that day's count). Both live under config/ on the
state-sync branch, so they never trigger a Render deploy.

NEEDS: FINNHUB_API_KEY in the environment for the Finnhub sources (without it only the RSS feeds are archived). No other credentials; nothing is sent anywhere except to the news sites.
"""
from __future__ import annotations

import json
import os
import threading
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

import requests

from state_paths import STATE_DIR, atomic_write_json, load_json_resilient

ARCHIVE_PATH = os.path.join(STATE_DIR, "headline_archive.json")
DAILY_REPO_DIR = "config/headlines"
ROLLING_DAYS = 7
MAX_HEADLINE_CHARS = 160
REQUEST_TIMEOUT = 15
FINNHUB_TICKERS = ("SPY", "QQQ")
RSS_FEEDS = {
    "yahoo_dji": "https://feeds.finance.yahoo.com/rss/2.0/headline?s=%5EDJI&region=US&lang=en-US",
    "yahoo_gspc": "https://feeds.finance.yahoo.com/rss/2.0/headline?s=%5EGSPC&region=US&lang=en-US",
    "yahoo_ixic": "https://feeds.finance.yahoo.com/rss/2.0/headline?s=%5EIXIC&region=US&lang=en-US",
    "marketwatch": "https://feeds.content.dowjones.io/public/rss/mw_topstories",
    "investing": "https://www.investing.com/rss/news.rss",
}
_UA = {"User-Agent": "Mozilla/5.0 (compatible; headline-archive)"}
_LOCK = threading.Lock()


def _key(headline: str) -> str:
    return " ".join(headline.lower().split())[:60]


def _clean(headline: str) -> str:
    return " ".join((headline or "").split())[:MAX_HEADLINE_CHARS]


def _day(t: int) -> str:
    return datetime.fromtimestamp(int(t), timezone.utc).strftime("%Y-%m-%d")


# ------------------------------------------------------------------ sources (each returns a list of {"t", "h", "s"} and raises on failure)

def parse_rss(xml_bytes: bytes, source: str) -> list:
    root = ET.fromstring(xml_bytes)
    out = []
    for item in root.findall(".//item"):
        title, stamp = item.findtext("title") or "", (item.findtext("pubDate") or "").strip()
        if not title or not stamp:
            continue
        try:
            when = parsedate_to_datetime(stamp)
        except (TypeError, ValueError):
            try:
                when = datetime.fromisoformat(stamp.replace(" ", "T"))
            except ValueError:
                continue
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        out.append({"t": int(when.timestamp()), "h": _clean(title), "s": source})
    return out


def fetch_rss(source: str, url: str) -> list:
    r = requests.get(url, headers=_UA, timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    return parse_rss(r.content, source)


def fetch_finnhub_general(token: str) -> list:
    r = requests.get("https://finnhub.io/api/v1/news", params={"category": "general", "token": token}, timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    return [{"t": int(x["datetime"]), "h": _clean(x.get("headline", "")), "s": "finnhub_general:" + str(x.get("source", ""))[:20]} for x in r.json() if isinstance(x, dict) and x.get("headline")]


def fetch_finnhub_company(token: str, symbol: str, day: str) -> list:
    r = requests.get("https://finnhub.io/api/v1/company-news", params={"symbol": symbol, "from": day, "to": day, "token": token}, timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    return [{"t": int(x["datetime"]), "h": _clean(x.get("headline", "")), "s": f"finnhub_{symbol}:" + str(x.get("source", ""))[:20]} for x in r.json() if isinstance(x, dict) and x.get("headline")]


def collect(now: datetime) -> tuple:
    """(items, report). Never raises: each source is isolated and its outcome recorded."""
    items, report = [], {}

    def run(name, fn):
        try:
            got = fn()
            items.extend(got)
            report[name] = len(got)
        except Exception as e:
            report[name] = f"error: {type(e).__name__}"

    token = os.environ.get("FINNHUB_API_KEY")
    if token:
        run("finnhub_general", lambda: fetch_finnhub_general(token))
        for sym in FINNHUB_TICKERS:
            for back in (0, 1):
                day = (now - timedelta(days=back)).strftime("%Y-%m-%d")
                run(f"finnhub_{sym}_{day}", lambda s=sym, d=day: fetch_finnhub_company(token, s, d))
    else:
        report["finnhub"] = "skipped: FINNHUB_API_KEY not set"
    for name, url in RSS_FEEDS.items():
        run(name, lambda n=name, u=url: fetch_rss(n, u))
    return items, report


# ------------------------------------------------------------------ archive

def empty_archive() -> dict:
    return {"items": [], "archived_counts": {}, "last_run": None}


def merge(archive: dict, new_items: list) -> int:
    """Adds items whose (first 60 chars, publication day) key is not already stored. Returns how many were new."""
    seen = {(_key(i["h"]), _day(i["t"])) for i in archive["items"]}
    added = 0
    for it in new_items:
        if not it.get("h") or not it.get("t"):
            continue
        k = (_key(it["h"]), _day(it["t"]))
        if k in seen:
            continue
        seen.add(k)
        archive["items"].append({"t": int(it["t"]), "h": it["h"], "s": it["s"]})
        added += 1
    archive["items"].sort(key=lambda i: i["t"])
    return added


def day_files_to_push(archive: dict, now: datetime) -> dict:
    """{day: [items]} for every COMPLETED UTC day whose stored count differs from what was last pushed (new days, or late-arriving items)."""
    today = now.strftime("%Y-%m-%d")
    by_day = {}
    for it in archive["items"]:
        by_day.setdefault(_day(it["t"]), []).append(it)
    return {d: rows for d, rows in by_day.items() if d < today and archive["archived_counts"].get(d) != len(rows)}


def prune(archive: dict, now: datetime) -> int:
    """Drops items older than ROLLING_DAYS, but only for days whose shard was pushed with the same count (never lose an unarchived day)."""
    cutoff = (now - timedelta(days=ROLLING_DAYS)).strftime("%Y-%m-%d")
    by_day = {}
    for it in archive["items"]:
        by_day.setdefault(_day(it["t"]), 0)
        by_day[_day(it["t"])] += 1
    drop = {d for d, n in by_day.items() if d < cutoff and archive["archived_counts"].get(d) == n}
    before = len(archive["items"])
    archive["items"] = [i for i in archive["items"] if _day(i["t"]) not in drop]
    return before - len(archive["items"])


def run_archive(now: datetime | None = None, collect_fn=None, push_day_fn=None, push_state_fn=None) -> dict:
    """One archive pass. Returns a small summary dict; never raises."""
    now = now or datetime.now(timezone.utc)
    summary = {"at": now.isoformat(), "new_items": 0, "days_pushed": [], "errors": []}
    try:
        with _LOCK:
            fresh_disk = not os.path.exists(ARCHIVE_PATH)
            archive = load_json_resilient(ARCHIVE_PATH, empty_archive())
            archive.setdefault("items", [])
            archive.setdefault("archived_counts", {})
            items, report = (collect_fn or collect)(now)
            summary["sources"] = report
            oldest_kept = (now - timedelta(days=ROLLING_DAYS - 1)).strftime("%Y-%m-%d")      # never re-create a day that was already pruned (its shard is complete)
            items = [i for i in items if i.get("t") and _day(i["t"]) >= oldest_kept]
            summary["new_items"] = merge(archive, items)
            for day, rows in sorted(day_files_to_push(archive, now).items()):
                try:
                    payload = json.dumps({"day": day, "items": rows}, separators=(",", ":")).encode("utf-8")
                    ok = (push_day_fn or _push_day)(payload, f"{DAILY_REPO_DIR}/{day}.json")
                    if ok:
                        archive["archived_counts"][day] = len(rows)
                        summary["days_pushed"].append(day)
                except Exception as e:
                    summary["errors"].append(f"push {day}: {type(e).__name__}")
            summary["pruned"] = prune(archive, now)
            archive["last_run"] = {"at": now.isoformat(), "new_items": summary["new_items"], "sources": report}
            atomic_write_json(ARCHIVE_PATH, archive, indent=None)
            try:
                (push_state_fn or _push_state)(ARCHIVE_PATH)
            except Exception as e:
                summary["errors"].append(f"state push: {type(e).__name__}")
            summary["fresh_disk"] = fresh_disk
    except Exception as e:  # the archive must never disturb the scheduler or the trading jobs
        summary["errors"].append(f"{type(e).__name__}: {str(e)[:120]}")
    return summary


def _push_day(payload: bytes, repo_path: str) -> bool:
    from github_state_sync import push_binary_file
    return bool(push_binary_file(payload, repo_path))


def _push_state(path: str) -> bool:
    from github_state_sync import push_state_to_github
    return bool(push_state_to_github(path))


def archive_headlines() -> None:
    """Scheduler entry point (hourly)."""
    summary = run_archive()
    bad = [k for k, v in (summary.get("sources") or {}).items() if isinstance(v, str) and v.startswith("error")]
    print(f"INFO: headline archive: {summary['new_items']} new headlines, days pushed {summary['days_pushed']}"
          + (f", source errors {bad}" if bad else "") + (f", problems {summary['errors']}" if summary["errors"] else ""), flush=True)
