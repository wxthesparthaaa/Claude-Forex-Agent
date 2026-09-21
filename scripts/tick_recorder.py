"""
Tick recorder -- records OANDA's live pricing stream (bid/ask ticks) to disk for offline research.

READ-ONLY: it only opens the pricing stream; it never places, changes or closes an order and never touches the
trading app's state. Run it on your own PC, separately from the Render app:

    scripts\\run_tick_recorder.bat            (or)   venv\\Scripts\\python.exe scripts\\tick_recorder.py
    venv\\Scripts\\python.exe scripts\\tick_recorder.py --check      # 30-second connection test, writes nothing

Why: round 1/2 of the scalp research showed no edge in one-minute bars; the open question is whether anything
exists at second-scale that survives the spread and a realistic reaction delay. That needs real ticks. Every row
keeps BOTH clocks -- when OANDA stamped the tick and when this PC received it -- so the delay can be modeled
honestly, and every disconnect/sleep is written to gaps.csv so analysis can exclude the holes.

Output (data/ticks/, override with TICK_DIR):
    YYYY-MM-DD.csv(.gz)  recv_ns,oanda_time,instrument,bid,ask,tradeable   (UTC day of the local receive time)
    gaps.csv             start_utc,end_utc,seconds,reason
    recorder.log         one line per event

Config (.env or environment): OANDA_ACCESS_TOKEN, OANDA_ACCOUNT_ID, OANDA_ENV (practice|live; default practice),
RECORDER_INSTRUMENTS (comma list; default = EUR_USD,GBP_USD,USD_JPY,AUD_USD,USD_CAD,XAU_USD).
"""
from __future__ import annotations

import gzip
import json
import os
import shutil
import statistics
import sys
import time
from datetime import datetime, timedelta, timezone

import requests

DEFAULT_INSTRUMENTS = ["EUR_USD", "GBP_USD", "USD_JPY", "AUD_USD", "USD_CAD", "XAU_USD"]
STREAM_URLS = {"practice": "https://stream-fxpractice.oanda.com", "live": "https://stream-fxtrade.oanda.com"}
READ_TIMEOUT_SECONDS = 15        # OANDA sends a heartbeat about every 5s; silence this long means a dead connection
FLUSH_EVERY_SECONDS = 5
STATUS_EVERY_SECONDS = 60
MIN_FREE_GB = 1.0
HEADER = "recv_ns,oanda_time,instrument,bid,ask,tradeable\n"


# --------------------------------------------------------------------------- pure helpers (unit tested)
def parse_stream_line(raw: bytes | str, recv_ns: int):
    """One line of OANDA's stream -> ('PRICE', csv_row, oanda_time) | ('HEARTBEAT', None, oanda_time) | None."""
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="replace")
    raw = raw.strip()
    if not raw:
        return None
    try:
        msg = json.loads(raw)
    except json.JSONDecodeError:
        return None
    kind = msg.get("type")
    if kind == "HEARTBEAT":
        return "HEARTBEAT", None, msg.get("time")
    if kind != "PRICE":
        return None
    try:
        bid = msg["bids"][0]["price"]
        ask = msg["asks"][0]["price"]
    except (KeyError, IndexError, TypeError):
        return None  # an entry with empty bids/asks (halted instrument) carries no price
    tradeable = 1 if msg.get("tradeable", msg.get("status") == "tradeable") else 0
    row = f"{recv_ns},{msg['time']},{msg['instrument']},{bid},{ask},{tradeable}\n"
    return "PRICE", row, msg["time"]


def utc_day(recv_ns: int) -> str:
    return datetime.fromtimestamp(recv_ns / 1e9, timezone.utc).strftime("%Y-%m-%d")


def clock_offset_ms(recv_ns: int, oanda_time: str) -> float:
    """recv - oanda in ms = network delay + (this PC's clock - OANDA's clock). Positive and steady is normal."""
    base, _, frac = oanda_time.rstrip("Z").partition(".")
    t = datetime.fromisoformat(base).replace(tzinfo=timezone.utc) + timedelta(microseconds=int((frac + "000000")[:6]))
    return recv_ns / 1e6 - t.timestamp() * 1000.0


class DayWriter:
    """Appends CSV rows to <dir>/<UTC day>.csv, rotating at UTC midnight and gzipping finished days."""

    def __init__(self, directory: str):
        self.dir = directory
        os.makedirs(directory, exist_ok=True)
        self.day = None
        self.fh = None
        self.last_flush = time.monotonic()
        self.compress_finished(current=None)

    def path_for(self, day: str) -> str:
        return os.path.join(self.dir, f"{day}.csv")

    def write(self, recv_ns: int, row: str) -> None:
        day = utc_day(recv_ns)
        if day != self.day:
            self.close()
            self.day = day
            path = self.path_for(day)
            new_file = not os.path.exists(path)
            self.fh = open(path, "a", encoding="utf-8", newline="")
            if new_file:
                self.fh.write(HEADER)
            self.compress_finished(current=day)
        self.fh.write(row)
        if time.monotonic() - self.last_flush >= FLUSH_EVERY_SECONDS:
            self.fh.flush()
            self.last_flush = time.monotonic()

    def close(self) -> None:
        if self.fh:
            self.fh.flush()
            self.fh.close()
            self.fh = None

    def compress_finished(self, current: str | None) -> None:
        for name in sorted(os.listdir(self.dir)):
            if name.endswith(".csv") and name[:-4] != current and name[:4].isdigit():
                src = os.path.join(self.dir, name)
                try:
                    with open(src, "rb") as f_in, gzip.open(src + ".gz", "wb") as f_out:
                        shutil.copyfileobj(f_in, f_out)
                    os.remove(src)
                except OSError as e:
                    log(self.dir, f"could not compress {name}: {e}")


def log(directory: str, message: str) -> None:
    line = f"{datetime.now(timezone.utc).isoformat(timespec='seconds')} {message}"
    print(line, flush=True)
    try:
        with open(os.path.join(directory, "recorder.log"), "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def record_gap(directory: str, start_ns: int, end_ns: int, reason: str) -> None:
    path = os.path.join(directory, "gaps.csv")
    new_file = not os.path.exists(path)
    iso = lambda ns: datetime.fromtimestamp(ns / 1e9, timezone.utc).isoformat(timespec="seconds")
    with open(path, "a", encoding="utf-8") as f:
        if new_file:
            f.write("start_utc,end_utc,seconds,reason\n")
        f.write(f"{iso(start_ns)},{iso(end_ns)},{(end_ns - start_ns) / 1e9:.0f},{reason.replace(',', ';')}\n")


# --------------------------------------------------------------------------- keep-awake (Windows)
ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001


def keep_system_awake(enable: bool) -> None:
    """Asks Windows not to sleep while recording. Deliberately NOT ES_DISPLAY_REQUIRED, so the screen still turns
    off (saves battery and the panel). Does not stop a lid-close sleep -- see the lid setting in the run notes."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED if enable else ES_CONTINUOUS)
    except Exception:
        pass


# --------------------------------------------------------------------------- streaming loop
def load_env() -> None:
    try:
        from dotenv import load_dotenv
        load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".env"),
                    encoding="utf-8-sig", override=True)
    except ImportError:
        pass


def stream_once(url: str, headers: dict, params: dict, writer: DayWriter | None, stats: dict, directory: str,
                stop_after_seconds: float | None = None):
    """One connection. Returns normally on clean end; raises on network failure."""
    with requests.get(url, headers=headers, params=params, stream=True, timeout=(10, READ_TIMEOUT_SECONDS)) as resp:
        resp.raise_for_status()
        started = time.monotonic()
        last_status = time.monotonic()
        stats["connected"] = True
        for raw in resp.iter_lines():
            recv_ns = time.time_ns()
            parsed = parse_stream_line(raw, recv_ns)
            if parsed is None:
                continue
            kind, row, oanda_time = parsed
            if stats["gap_start_ns"] is not None:
                if writer is not None:
                    record_gap(directory, stats["gap_start_ns"], recv_ns, stats["gap_reason"])
                log(directory, f"reconnected after {(recv_ns - stats['gap_start_ns']) / 1e9:.0f}s")
                stats["gap_start_ns"], stats["backoff"] = None, 1.0
            stats["last_recv_ns"] = recv_ns
            if kind == "PRICE":
                stats["ticks"] += 1
                stats["per_instrument"][row.split(",")[2]] = stats["per_instrument"].get(row.split(",")[2], 0) + 1
                if len(stats["offsets"]) < 2000:
                    try:
                        stats["offsets"].append(clock_offset_ms(recv_ns, oanda_time))
                    except ValueError:
                        pass
                if writer is not None:
                    writer.write(recv_ns, row)
            if time.monotonic() - last_status >= STATUS_EVERY_SECONDS:
                last_status = time.monotonic()
                free_gb = shutil.disk_usage(directory).free / 1e9
                log(directory, f"status: {stats['ticks']} ticks recorded, "
                               f"{', '.join(f'{k} {v}' for k, v in sorted(stats['per_instrument'].items()))}"
                               f"{'' if free_gb >= MIN_FREE_GB else f' | LOW DISK: {free_gb:.1f} GB free'}")
            if stop_after_seconds is not None and time.monotonic() - started >= stop_after_seconds:
                return


def run(check_only: bool = False) -> int:
    load_env()
    token, account = os.environ.get("OANDA_ACCESS_TOKEN"), os.environ.get("OANDA_ACCOUNT_ID")
    if not token or not account:
        print("OANDA_ACCESS_TOKEN / OANDA_ACCOUNT_ID are not set (put them in .env). Nothing recorded.")
        return 2
    env = os.environ.get("OANDA_ENV", "practice")
    instruments = [i.strip() for i in os.environ.get("RECORDER_INSTRUMENTS", "").split(",") if i.strip()] or DEFAULT_INSTRUMENTS
    directory = os.environ.get("TICK_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "ticks"))
    directory = os.path.abspath(directory)
    os.makedirs(directory, exist_ok=True)

    url = f"{STREAM_URLS.get(env, STREAM_URLS['practice'])}/v3/accounts/{account}/pricing/stream"
    headers = {"Authorization": f"Bearer {token}"}
    params = {"instruments": ",".join(instruments), "snapshot": "true"}
    stats = {"ticks": 0, "per_instrument": {}, "offsets": [], "connected": False, "last_recv_ns": None,
             "gap_start_ns": None, "gap_reason": "", "backoff": 1.0}
    writer = None if check_only else DayWriter(directory)

    if check_only:
        print(f"Checking the {env} pricing stream for 30 seconds ({', '.join(instruments)}); nothing is written...")
    else:
        log(directory, f"recorder started: {', '.join(instruments)} ({env}) -> {directory}")
        keep_system_awake(True)

    try:
        while True:
            try:
                stream_once(url, headers, params, writer, stats, directory, 30 if check_only else None)
                if check_only:
                    break
                reason = "stream ended by server"
            except KeyboardInterrupt:
                raise
            except requests.RequestException as e:
                reason = f"{type(e).__name__}: {str(e)[:120]}"
            except Exception as e:  # never let one bad line/connection kill a multi-week run
                reason = f"unexpected {type(e).__name__}: {str(e)[:120]}"
            if check_only:
                print(f"Connection problem: {reason}")
                return 1
            if stats["gap_start_ns"] is None:
                stats["gap_start_ns"] = stats["last_recv_ns"] or time.time_ns()
            stats["gap_reason"] = reason
            log(directory, f"disconnected ({reason}); retrying in {stats['backoff']:.0f}s")
            time.sleep(stats["backoff"])
            stats["backoff"] = min(stats["backoff"] * 2, 60.0)
    except KeyboardInterrupt:
        pass
    finally:
        if writer:
            writer.close()
            log(directory, "recorder stopped")
        keep_system_awake(False)

    if check_only:
        if stats["ticks"] == 0:
            print("Connected but received no prices (market closed, or the account has no access to these pairs).")
            return 1
        offs = stats["offsets"]
        print(f"OK: {stats['ticks']} ticks in 30s ({', '.join(f'{k} {v}' for k, v in sorted(stats['per_instrument'].items()))}).")
        if offs:
            print(f"PC receive time minus OANDA time: median {statistics.median(offs):.0f} ms "
                  f"(range {min(offs):.0f} to {max(offs):.0f} ms). Network delay plus any clock error; "
                  f"steady and positive is good. Large or negative values mean sync this PC's clock.")
    return 0


if __name__ == "__main__":
    sys.exit(run(check_only="--check" in sys.argv))
