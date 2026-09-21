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
    clock.csv            utc,server,local_ahead_ms,rtt_ms  -- this PC's clock error vs public NTP, sampled every 10 min
    recorder.log         one line per event
    heartbeat.txt        refreshed every 10s -- lets the next start tell how long the recorder was NOT running

Telegram alerts (same bot as the trading app; set RECORDER_ALERTS=off to disable, --test-telegram to test):
    PAUSED   the stream has been down 60s+ (sent as soon as the internet allows -- queued and retried if it is down)
    RESUMED  recording is back, with exactly which minutes are missing and why
    SILENT   connected but no prices for 5 min while the forex market is open
    STARTED  every start, plus how long it was not running before (covers sleep/shutdown/closed window)
    STOPPED  clean stop (Ctrl+C); LOW DISK
A recorder that is killed outright or a PC that is off cannot announce it -- but the STARTED message afterwards does.

Config (.env or environment): OANDA_ACCESS_TOKEN, OANDA_ACCOUNT_ID, OANDA_ENV (practice|live; default practice),
RECORDER_INSTRUMENTS (comma list; default = EUR_USD,GBP_USD,USD_JPY,AUD_USD,USD_CAD,XAU_USD).
"""
from __future__ import annotations

import gzip
import json
import os
import queue
import shutil
import socket
import statistics
import struct
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests

DEFAULT_INSTRUMENTS = ["EUR_USD", "GBP_USD", "USD_JPY", "AUD_USD", "USD_CAD", "XAU_USD"]
STREAM_URLS = {"practice": "https://stream-fxpractice.oanda.com", "live": "https://stream-fxtrade.oanda.com"}
READ_TIMEOUT_SECONDS = 15        # OANDA sends a heartbeat about every 5s; silence this long means a dead connection
FLUSH_EVERY_SECONDS = 5
STATUS_EVERY_SECONDS = 60
LIVE_LINE_EVERY_SECONDS = 5
MIN_FREE_GB = 1.0
NTP_SERVERS = ["time.cloudflare.com", "time.google.com", "pool.ntp.org"]
NTP_EPOCH_OFFSET = 2208988800
CLOCK_SAMPLE_EVERY_SECONDS = 600
OUTAGE_ALERT_SECONDS = 60          # a blip shorter than this is only logged, not announced
SILENCE_ALERT_SECONDS = 300        # connected but no PRICE this long while the market is open
WATCHDOG_EVERY_SECONDS = 10
FROZEN_DETECT_SECONDS = 60         # the 10s watchdog loop took this much longer than planned => PC/process was suspended
NEW_RUN_GAP_MINUTES = 3            # a start this long after the last heartbeat is reported as a restart with a gap
SGT = ZoneInfo("Asia/Singapore")
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

    def flush(self) -> None:
        if self.fh:
            self.fh.flush()
            self.last_flush = time.monotonic()

    def close(self) -> None:
        if self.fh:
            self.fh.flush()
            self.fh.close()
            self.fh = None

    def compress_finished(self, current: str | None) -> None:
        """Gzips only days strictly BEFORE `current` (default: today, UTC) -- never the day still being written, so a
        restart mid-day cannot pack away the file we are about to append to -- and never overwrites an existing .gz
        (a same-day restart leaves an earlier part behind); extra parts get .1, .2 ... in the name."""
        cutoff = current or utc_day(time.time_ns())
        for name in sorted(os.listdir(self.dir)):
            if name.endswith(".csv") and name[:4].isdigit() and name[:-4] < cutoff:
                src = os.path.join(self.dir, name)
                dst, n = src + ".gz", 0
                while os.path.exists(dst):
                    n += 1
                    dst = os.path.join(self.dir, f"{name[:-4]}.{n}.csv.gz")
                try:
                    with open(src, "rb") as f_in, gzip.open(dst, "wb") as f_out:
                        shutil.copyfileobj(f_in, f_out)
                    os.remove(src)
                except OSError as e:
                    log(self.dir, f"could not compress {name}: {e}")


_live_line_open = False


def live_line(text: str) -> None:
    """A single console line that updates in place (not written to recorder.log)."""
    global _live_line_open
    print("\r" + text.ljust(100), end="", flush=True)
    _live_line_open = True


def log(directory: str, message: str) -> None:
    global _live_line_open
    line = f"{datetime.now(timezone.utc).isoformat(timespec='seconds')} {message}"
    if _live_line_open:
        print(flush=True)  # finish the in-place line before a permanent log line
        _live_line_open = False
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


# --------------------------------------------------------------------------- clock error vs NTP
def ntp_offset_from_packet(reply: bytes, t1: float, t4: float):
    """SNTP maths. t1 = local send time, t4 = local receive time (unix seconds). Returns (local_ahead_ms, rtt_ms)."""
    def ts(b):
        secs, frac = struct.unpack("!II", b)
        return secs - NTP_EPOCH_OFFSET + frac / 2**32
    t2, t3 = ts(reply[32:40]), ts(reply[40:48])
    theta = ((t2 - t1) + (t3 - t4)) / 2          # server minus local
    rtt = (t4 - t1) - (t3 - t2)
    return -theta * 1000.0, rtt * 1000.0


def measure_clock_error(servers=NTP_SERVERS, samples: int = 3):
    """Median (local_ahead_ms, rtt_ms, server) over a few SNTP queries, or None if none answer. local_ahead > 0 means
    this PC's clock is AHEAD of true time."""
    for server in servers:
        results = []
        for _ in range(samples):
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                    sock.settimeout(2.0)
                    packet = b"\x1b" + 47 * b"\x00"
                    t1 = time.time()
                    sock.sendto(packet, (server, 123))
                    reply, _ = sock.recvfrom(512)
                    t4 = time.time()
                if len(reply) >= 48:
                    results.append(ntp_offset_from_packet(reply, t1, t4))
            except OSError:
                continue
            time.sleep(0.2)
        if results:
            results.sort(key=lambda r: r[1])          # trust the lowest-round-trip answers
            best = results[: max(1, len(results) // 2 + 1)]
            return statistics.median(r[0] for r in best), statistics.median(r[1] for r in best), server
    return None


def clock_sampler(directory: str, stop: threading.Event) -> None:
    path = os.path.join(directory, "clock.csv")
    while not stop.is_set():
        result = measure_clock_error()
        if result is not None:
            ahead, rtt, server = result
            new_file = not os.path.exists(path)
            with open(path, "a", encoding="utf-8") as f:
                if new_file:
                    f.write("utc,server,local_ahead_ms,rtt_ms\n")
                f.write(f"{datetime.now(timezone.utc).isoformat(timespec='seconds')},{server},{ahead:.0f},{rtt:.0f}\n")
        stop.wait(CLOCK_SAMPLE_EVERY_SECONDS)


# --------------------------------------------------------------------------- alerts + self-check
def sgt_time(ns: int) -> str:
    return datetime.fromtimestamp(ns / 1e9, SGT).strftime("%H:%M")


def sgt_span(start_ns: int, end_ns: int) -> str:
    return f"{sgt_time(start_ns)}-{sgt_time(end_ns)} SGT ({(end_ns - start_ns) / 60e9:.0f} min)"


class Notifier:
    """Best-effort Telegram alerts. Messages are queued and retried, so an alert raised while the internet is down
    is delivered as soon as it returns instead of being lost. Never raises into the recording loop."""

    def __init__(self, sender=None, retry_seconds: float = 15.0):
        self.sender = sender or self._telegram_sender
        self.retry_seconds = retry_seconds
        self.pending = queue.Queue()
        self.stop_event = threading.Event()
        self.enabled = True
        self.thread = threading.Thread(target=self._run, daemon=True)

    @staticmethod
    def _telegram_sender(text: str) -> None:
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
        from telegram_notifier import send_message
        send_message(text)

    def start(self) -> None:
        self.thread.start()

    def send(self, text: str) -> None:
        if self.enabled:
            self.pending.put(text)

    def send_now(self, text: str) -> bool:
        """One immediate attempt (used for the clean-stop message, when the queue thread is about to end)."""
        try:
            self.sender(text)
            return True
        except Exception:
            return False

    def _run(self) -> None:
        while not self.stop_event.is_set():
            try:
                text = self.pending.get(timeout=1)
            except queue.Empty:
                continue
            while not self.stop_event.is_set():
                try:
                    self.sender(text)
                    break
                except (FileNotFoundError, KeyError):
                    self.enabled = False          # no Telegram config on this PC: stop trying, keep recording
                    print("Telegram is not configured on this PC -- alerts disabled (recording is unaffected).", flush=True)
                    return
                except Exception:
                    self.stop_event.wait(self.retry_seconds)   # keep the alert and retry shortly

    def stop(self) -> None:
        self.stop_event.set()


def evaluate_health(stats: dict, now_ns: int, market_open) -> list:
    """The self-check. Pure decision logic (unit tested): returns the alert messages due right now and updates the
    alerted flags in `stats` so each problem is announced once. market_open may be None (unknown) -> no silence alerts."""
    msgs = []
    gap = stats.get("gap_start_ns")
    if gap is not None and not stats.get("alerted_outage") and now_ns - gap >= OUTAGE_ALERT_SECONDS * 10**9:
        stats["alerted_outage"] = True
        msgs.append(
            f"⚠️ <b>Tick recorder PAUSED</b>\nThe price stream has been down since {sgt_time(gap)} SGT "
            f"({(now_ns - gap) / 60e9:.0f} min so far): {stats.get('gap_reason', 'unknown reason')}.\n"
            f"Retrying automatically. Everything recorded before the pause is saved.")
    last_price = stats.get("last_price_ns")
    if gap is None and market_open and last_price is not None and not stats.get("alerted_silence") \
            and now_ns - last_price >= SILENCE_ALERT_SECONDS * 10**9:
        stats["alerted_silence"] = True
        msgs.append(
            f"⚠️ <b>Tick recorder SILENT</b>\nStill connected, but no prices for "
            f"{(now_ns - last_price) / 60e9:.0f} min while the forex market is open (last price {sgt_time(last_price)} SGT).")
    if stats.get("alerted_silence") and last_price is not None and now_ns - last_price < 60 * 10**9:
        stats["alerted_silence"] = False
        msgs.append("✅ <b>Tick recorder</b>: prices are flowing again.")
    return msgs


def resume_message(stats: dict, gap_start_ns: int, end_ns: int):
    """Sent when a gap closes -- but only if it was announced or is long enough to matter."""
    if not (stats.get("alerted_outage") or (end_ns - gap_start_ns) >= OUTAGE_ALERT_SECONDS * 10**9):
        return None
    note = f"\nNote: {stats['frozen_note']}" if stats.get("frozen_note") else ""
    return (f"✅ <b>Tick recorder RESUMED</b>\nMissing data: {sgt_span(gap_start_ns, end_ns)}. "
            f"Cause: {stats.get('gap_reason', 'unknown')}.{note}")


def market_is_open():
    try:
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
        from market_hours import is_forex_market_open
        return bool(is_forex_market_open())
    except Exception:
        return None


def read_heartbeat(directory: str):
    try:
        with open(os.path.join(directory, "heartbeat.txt"), encoding="utf-8") as f:
            return datetime.fromisoformat(f.read().strip())
    except (OSError, ValueError):
        return None


def watchdog(stats: dict, directory: str, notifier: Notifier, stop: threading.Event) -> None:
    path = os.path.join(directory, "heartbeat.txt")
    last_wall = time.time()
    while not stop.wait(WATCHDOG_EVERY_SECONDS):
        wall = time.time()
        late = (wall - last_wall) - WATCHDOG_EVERY_SECONDS
        last_wall = wall
        if late >= FROZEN_DETECT_SECONDS:
            stats["frozen_note"] = f"this PC (or the recorder) was frozen or asleep for about {late / 60:.0f} min"
            log(directory, f"watchdog: {stats['frozen_note']}")
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(datetime.now(timezone.utc).isoformat(timespec="seconds"))
        except OSError:
            pass
        for msg in evaluate_health(stats, time.time_ns(), market_is_open()):
            log(directory, "alert: " + msg.replace("\n", " | ").replace("<b>", "").replace("</b>", ""))
            notifier.send(msg)


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
                message = resume_message(stats, stats["gap_start_ns"], recv_ns)
                if message and stats.get("notify"):
                    stats["notify"](message)
                stats["gap_start_ns"], stats["backoff"] = None, 1.0
                stats["alerted_outage"], stats["frozen_note"] = False, None
            stats["last_recv_ns"] = recv_ns
            if kind == "PRICE":
                stats["ticks"] += 1
                stats["last_price_ns"] = recv_ns
                stats["per_instrument"][row.split(",")[2]] = stats["per_instrument"].get(row.split(",")[2], 0) + 1
                if len(stats["offsets"]) < 2000:
                    try:
                        stats["offsets"].append(clock_offset_ms(recv_ns, oanda_time))
                    except ValueError:
                        pass
                if writer is not None:
                    writer.write(recv_ns, row)
            if writer is not None and time.monotonic() - stats.get("last_live", 0.0) >= LIVE_LINE_EVERY_SECONDS:
                gained = stats["ticks"] - stats.get("last_live_ticks", 0)
                stats["last_live"], stats["last_live_ticks"] = time.monotonic(), stats["ticks"]
                live_line(f"[{datetime.now().strftime('%H:%M:%S')}] recording OK -- {stats['ticks']:,} ticks this session "
                          f"(+{gained} in the last {LIVE_LINE_EVERY_SECONDS}s). Leave this window open.")
            if time.monotonic() - last_status >= STATUS_EVERY_SECONDS:
                last_status = time.monotonic()
                free_gb = shutil.disk_usage(directory).free / 1e9
                if free_gb < MIN_FREE_GB and not stats.get("low_disk_alerted") and stats.get("notify"):
                    stats["low_disk_alerted"] = True
                    stats["notify"](f"\u26a0\ufe0f <b>Tick recorder</b>: low disk space ({free_gb:.1f} GB free).")
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
    clock_stop = threading.Event()
    notifier = Notifier()
    alerts_on = (not check_only) and os.environ.get("RECORDER_ALERTS", "on").lower() != "off"

    if check_only:
        print(f"Checking the {env} pricing stream for 30 seconds ({', '.join(instruments)}); nothing is written...")
    else:
        log(directory, f"recorder started: {', '.join(instruments)} ({env}) -> {directory}")
        keep_system_awake(True)
        threading.Thread(target=clock_sampler, args=(directory, clock_stop), daemon=True).start()
        if alerts_on:
            notifier.start()
            stats["notify"] = notifier.send
            previous = read_heartbeat(directory)
            now_utc = datetime.now(timezone.utc)
            was_down = ""
            if previous is not None and (now_utc - previous) > timedelta(minutes=NEW_RUN_GAP_MINUTES):
                was_down = (f"\nIt was NOT recording from {previous.astimezone(SGT):%H:%M} to "
                            f"{now_utc.astimezone(SGT):%H:%M} SGT ({(now_utc - previous).total_seconds() / 60:.0f} min).")
            notifier.send(f"\u25b6\ufe0f <b>Tick recorder STARTED</b>\n{', '.join(instruments)} ({env}).{was_down}")
        threading.Thread(target=watchdog, args=(stats, directory, notifier, clock_stop), daemon=True).start()

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
            if writer:
                writer.flush()          # everything received so far is on disk before we wait to reconnect
            if stats["gap_start_ns"] is None:
                stats["gap_start_ns"] = stats["last_recv_ns"] or time.time_ns()
            stats["gap_reason"] = reason
            log(directory, f"disconnected ({reason}); retrying in {stats['backoff']:.0f}s")
            time.sleep(stats["backoff"])
            stats["backoff"] = min(stats["backoff"] * 2, 60.0)
    except KeyboardInterrupt:
        pass
    finally:
        clock_stop.set()
        if writer:
            writer.close()
            log(directory, "recorder stopped")
            if alerts_on:
                notifier.send_now("\u23f9\ufe0f <b>Tick recorder STOPPED</b> (clean shutdown -- recording is not running now).")
        notifier.stop()
        keep_system_awake(False)

    if check_only:
        if stats["ticks"] == 0:
            print("Connected but received no prices (market closed, or the account has no access to these pairs).")
            return 1
        offs = stats["offsets"]
        print(f"OK: {stats['ticks']} ticks in 30s ({', '.join(f'{k} {v}' for k, v in sorted(stats['per_instrument'].items()))}).")
        if offs:
            raw = statistics.median(offs)
            print(f"PC receive time minus OANDA time: median {raw:.0f} ms (network delay plus any clock error).")
            clock = measure_clock_error()
            if clock is None:
                print("Could not reach an NTP server to measure this PC's clock error (UDP port 123 blocked?).")
            else:
                ahead, rtt, server = clock
                print(f"This PC's clock vs {server}: {'ahead' if ahead > 0 else 'behind'} by {abs(ahead):.0f} ms "
                      f"(NTP round trip {rtt:.0f} ms).")
                print(f"=> real delay from OANDA stamping a price to this PC receiving it: about {raw - ahead:.0f} ms. "
                      f"The recorder logs the clock error every 10 minutes (clock.csv) so the data is corrected either way.")
    return 0


def test_telegram() -> int:
    try:
        Notifier._telegram_sender("\U0001F9EA <b>Tick recorder</b>: test alert -- if you can read this, alerts work.")
    except Exception as e:
        print(f"Telegram test FAILED: {type(e).__name__}: {e}")
        return 1
    print("Telegram test sent -- check your chat.")
    return 0


if __name__ == "__main__":
    if "--test-telegram" in sys.argv:
        sys.exit(test_telegram())
    sys.exit(run(check_only="--check" in sys.argv))
