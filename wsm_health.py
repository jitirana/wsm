#!/usr/bin/env python3
"""wsm_health.py - self-healing and health alerts (ntfy) for WSM.

What it does (a monitor thread inside the service, checks every `check_s` seconds):
  * RADIO STALL / HANG: the RSP2 stream stops (stuck in sdrplay_api_Open, no slot ever recorded, "no samples" slots).
    Self-heal, escalating:  1st time -> restart the WSM process (systemd brings it back);
                            2nd time -> restart WSM and ask systemd to restart the `sdrplay` API service first;
                            more than `max_restarts` in `window_min` minutes -> stop restarting and alert for manual action.
  * ALERTS (ntfy, onset + reminder every `repeat_h` + "recovered"): radio stalled, healing failed, no spots for `no_spots_hours`,
    low disk space, big database, system clock off, POWER (under-voltage), TEMPERATURE, SAMPLE LOSS (slot coverage). Optional heartbeat URL (e.g. healthchecks.io) for "the whole box is dead".
The ntfy topic and the heartbeat URL are secrets: they live only in wsm.yaml and are never logged.

wsm.yaml (all optional):
  health:
    enabled: yes
    ntfy_topic: ""          # default: daily_report.ntfy_topic
    self_heal: yes          # no = only alert, never restart
    stall_min: 10           # no good slot for this long = radio stall
    opening_s: 90           # stuck "opening" for this long = hang
    hang_s: 300             # a slot overdue by this long = hang
    max_restarts: 2         # self-heal restarts allowed per window_min
    window_min: 60
    repeat_h: 6             # reminder interval of an alert that is still active
    no_spots_hours: 0       # alert when no spot was decoded for this many hours (0 = off; set it once you hear the band)
    disk_min_mb: 500
    db_max_mb: 1000
    clock_max_s: 2.0
    power_events_max: 10    # under-voltage events per hour (or 5 % of the time) that raise the "power" alert (Raspberry Pi only)
    temp_max_c: 75          # CPU temperature alert (cleared 3 C below)
    coverage_min: 95        # average slot coverage (%) in the last hour below this = samples are being lost
    heartbeat_url: ""       # pinged every heartbeat_min while the radio works (url + "/fail" when it does not)
    heartbeat_min: 5

systemd: the 2nd escalation step needs the ExecStartPre line of deploy/wsm.service (restarts `sdrplay` when data/.restart_sdrplay exists).
Test:  python3 wsm_health.py --test      send a test notification
       python3 wsm_health.py --status    settings and the active alerts
"""
import argparse
import json
import os
import shutil
import socket
import struct
import subprocess
import threading
import time
import urllib.parse
import urllib.request

import wsm_config

UA = "wsm-wspr-system-monitor/1.0"
RESTART_EXIT = 3
DEFAULTS = dict(
    enabled=True, ntfy_topic="", ntfy_server="https://ntfy.sh", self_heal=True, check_s=15,
    stall_min=10, opening_s=90, hang_s=300, max_restarts=2, window_min=60, repeat_h=6,
    no_spots_hours=0, disk_min_mb=500, db_max_mb=1000, clock_max_s=2.0, heartbeat_url="", heartbeat_min=5,
    power_events_max=10, temp_max_c=75.0, coverage_min=95.0,
)


def _bool(v):
    return v.strip().lower() in ("1", "true", "yes", "y", "on", "sim", "s") if isinstance(v, str) else bool(v)


def settings(cfg):
    s = dict(DEFAULTS)
    s.update(cfg.get("health") or {})
    for k in ("enabled", "self_heal"):
        s[k] = _bool(s[k])
    for k in ("check_s", "stall_min", "opening_s", "hang_s", "max_restarts", "window_min", "repeat_h", "no_spots_hours",
              "disk_min_mb", "db_max_mb", "clock_max_s", "heartbeat_min", "power_events_max", "temp_max_c", "coverage_min"):
        s[k] = float(s[k])
    topic = str(s["ntfy_topic"] or "").strip()
    server = str(s["ntfy_server"]).rstrip("/")
    if not topic:                                         # fall back to the daily report's topic
        d = cfg.get("daily_report") or {}
        topic = str(d.get("ntfy_topic") or "").strip()
        if d.get("ntfy_server"):
            server = str(d["ntfy_server"]).rstrip("/")
    if "/" in topic:                                      # accept "ntfy.sh/NAME" or "https://ntfy.sh/NAME"
        p = urllib.parse.urlparse(topic if "://" in topic else "https://" + topic)
        server, topic = f"{p.scheme}://{p.netloc}", p.path.strip("/")
    s["ntfy_topic"], s["ntfy_server"] = topic, server
    s["heartbeat_url"] = str(s["heartbeat_url"] or "").strip()
    return s


def ntfy(st, title, message, priority=3, tags=("satellite_antenna",)):
    """ntfy JSON publish; the topic travels in the request body and is never logged"""
    body = json.dumps(dict(topic=st["ntfy_topic"], title=title, message=message, priority=priority, tags=list(tags))).encode()
    req = urllib.request.Request(st["ntfy_server"] + "/", data=body, headers={"Content-Type": "application/json", "User-Agent": UA})
    with urllib.request.urlopen(req, timeout=20) as r:
        if r.status // 100 != 2:
            raise RuntimeError(f"ntfy answered {r.status}")


def sntp_offset(host="pool.ntp.org", timeout=4.0):
    """clock offset in seconds (server - local) or None"""
    try:
        pkt = b"\x1b" + 47 * b"\0"
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.settimeout(timeout)
            t0 = time.time()
            s.sendto(pkt, (host, 123))
            data, _ = s.recvfrom(512)
            t1 = time.time()
        sec, frac = struct.unpack("!II", data[40:48])
        srv = sec - 2208988800 + frac / 2 ** 32
        return srv - (t0 + t1) / 2
    except Exception:
        return None


def ntp_synced():
    """True/False from timedatectl, None when unknown"""
    try:
        out = subprocess.run(["timedatectl", "show", "-p", "NTPSynchronized", "--value"], capture_output=True, text=True, timeout=5).stdout.strip()
        return {"yes": True, "no": False}.get(out)
    except Exception:
        return None


def dur(sec):
    sec = int(sec)
    return f"{sec // 3600} h {sec % 3600 // 60} min" if sec >= 3600 else f"{sec // 60} min {sec % 60} s"


class Monitor:
    def __init__(self, cfg, db, state, stop, log=print, send=None, restart=None, clock=time.time, offset_fn=sntp_offset, synced_fn=ntp_synced):
        self.cfg, self.db, self.state, self.stop, self.log = cfg, db, state, stop, log
        self.st = settings(cfg)
        self.send = send or (lambda title, msg, prio, tags: ntfy(self.st, title, msg, prio, tags))
        self.restart = restart or (lambda: os._exit(RESTART_EXIT))
        self.clock, self.offset_fn, self.synced_fn = clock, offset_fn, synced_fn
        self.started = clock()
        self.seen = {}
        self.path = os.path.join(cfg["data_dir"], "health.json")
        self.h = self._load()
        self.h.setdefault("alerts", {})
        self.h.setdefault("restarts", [])
        self.retry_at = 0.0
        self.last_clock = 0.0
        self.clock_bad = False
        self.last_beat = 0.0

    # ----- persistence -----
    def _load(self):
        try:
            return json.load(open(self.path))
        except Exception:
            return {}

    def _save(self):
        tmp = self.path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self.h, f)
        os.replace(tmp, self.path)

    # ----- notifications -----
    def notify(self, title, msg, prio=3, tags=("satellite_antenna",)):
        if not self.st["ntfy_topic"]:
            return False
        try:
            self.send("WSM: " + title, msg, prio, list(tags))
            return True
        except Exception as e:
            self.log(f"health: notification failed ({type(e).__name__})")
            return False

    def alert(self, key, bad, title, msg, critical=True):
        """onset / reminder / recovery of one named condition"""
        now, a = self.clock(), self.h["alerts"]
        cur = a.get(key)
        if bad and not cur:
            if now < self.retry_at:
                return
            if self.notify(title, msg, 4 if critical else 3, ("warning",)):
                a[key] = dict(since=now, last=now, title=title)
                self._save()
                self.log(f"health: ALERT {key}: {title}")
            else:
                self.retry_at = now + 60
                if not self.st["ntfy_topic"]:
                    a[key] = dict(since=now, last=now, title=title, silent=True)   # remembered, but nobody to tell
                    self.log(f"health: ALERT {key}: {title} (no ntfy topic configured)")
        elif bad and cur:
            if now - cur["last"] >= self.st["repeat_h"] * 3600 and now >= self.retry_at:
                if cur.get("silent") or self.notify(title + " (still active)", f"{msg}\nActive for {dur(now - cur['since'])}.", 3, ("warning",)):
                    cur["last"] = now
                    self._save()
                else:
                    self.retry_at = now + 60
        elif not bad and cur:
            if not cur.get("silent"):
                if not self.notify("recovered: " + cur["title"].replace("WSM: ", ""), f"Back to normal after {dur(now - cur['since'])}.", 3, ("white_check_mark",)):
                    self.retry_at = now + 60
                    return
            del a[key]
            self._save()
            self.log(f"health: recovered {key}")

    # ----- checks -----
    def last_good_ts(self):
        for r in self.db.recent_slots(40):
            if r["status"] != "error" or str(r.get("note") or "").startswith("decoder"):
                return r["ts"]
        return 0

    def hangs(self, now):
        out = []
        for r in self.state.snapshot():
            name, s, nxt = r["name"], r["state"], r.get("next_ts")
            key = (s, r.get("band"), nxt)
            prev = self.seen.get(name)
            if not prev or prev[0] != key:
                self.seen[name] = (key, now)
            since = now - self.seen[name][1]
            if s == "opening" and since > self.st["opening_s"]:
                out.append(f"{name}: stuck opening the RSP2 for {dur(since)}")
            elif s in ("waiting", "listening") and nxt and now > nxt + self.st["hang_s"]:
                out.append(f"{name}: slot overdue by {dur(now - nxt)}")
        return out

    def heal(self, why, now):
        w = self.st["window_min"] * 60
        rs = [t for t in self.h["restarts"] if now - t < w]
        if not self.st["self_heal"] or len(rs) >= self.st["max_restarts"]:
            self.alert("heal_failed", True, "radio problem, manual action needed",
                       f"{why}\nAutomatic recovery " + ("is off." if not self.st["self_heal"] else f"was tried {len(rs)} time(s) in the last {self.st['window_min']:.0f} min without success.")
                       + "\nTry: sudo systemctl restart sdrplay wsm, then check the USB cable/port.")
            return False
        attempt = len(rs) + 1
        self.h["restarts"] = rs + [now]
        if attempt >= 2:
            try:
                open(os.path.join(self.cfg["data_dir"], ".restart_sdrplay"), "w").close()
            except OSError as e:
                self.log(f"health: cannot write the sdrplay flag ({e})")
        self.h["pending"] = dict(ts=now, text=f"{why}\nAutomatic restart #{attempt}" + (" (with the sdrplay service)." if attempt >= 2 else "."))
        self._save()
        self.log(f"health: self-heal #{attempt}: {why}; restarting WSM")
        self.restart()
        return True

    def check_system(self, s):
        st, a = self.st, self.h["alerts"]
        if s.get("uv_events_1h") is not None and s.get("samples_1h", 0) >= 30:          # >= 5 min of samples
            ev, pct = s["uv_events_1h"], s["uv_pct_1h"] or 0.0
            bad = ev >= st["power_events_max"] or pct >= 5.0
            if "power" in a:                                                           # clear only when clearly better
                bad = ev >= st["power_events_max"] / 2 or pct >= 2.0
            self.alert("power", bad, "under-voltage (power supply)",
                       f"{ev} under-voltage event(s) in the last hour, {pct:.0f} % of the time in under-voltage; the CPU was held "
                       f"below 1 GHz {s.get('low_clock_pct_1h') or 0:.0f} % of the time. Check the power supply and its cable.")
        t = s.get("temp_c")
        if t is not None:
            bad = t > st["temp_max_c"] or ("temp" in a and t > st["temp_max_c"] - 3)
            self.alert("temp", bad, "computer is too hot",
                       f"CPU at {t:.1f} C (alert above {st['temp_max_c']:.0f} C). Check the fan / ventilation.", False)
        cov = s.get("coverage_1h")
        if cov is not None and s.get("slots_1h", 0) >= 5:
            bad = cov < st["coverage_min"] or ("coverage" in a and cov < min(99.0, st["coverage_min"] + 2))
            self.alert("coverage", bad, "samples are being lost",
                       f"Average slot coverage {cov:.0f} % in the last hour ({s.get('partial_1h', 0)} of {s['slots_1h']} slots "
                       f"incomplete). Usually power or CPU: see the System card on the page.")

    def step(self):
        now, st = self.clock(), self.st
        if "pending" in self.h and now - self.started > 20:      # told once, after the restart
            p = self.h["pending"]
            if self.notify("restarted itself", p["text"], 3, ("arrows_counterclockwise",)):
                del self.h["pending"]
                self._save()
        # radio
        age = now - max(self.last_good_ts(), self.started)
        hang = self.hangs(now)
        stalled = age > st["stall_min"] * 60
        why = "; ".join(hang) or (f"no good slot for {dur(age)}" if stalled else "")
        if hang or stalled:
            self.alert("radio", True, "radio stalled", why + ("\nSelf-heal will restart the service." if st["self_heal"] else ""))
            self.heal(why, now)
        else:                                                    # recovered only once a good slot arrived after the alert began
            cur = self.h["alerts"].get("radio") or self.h["alerts"].get("heal_failed")
            if not cur or self.last_good_ts() > cur["since"]:
                self.alert("radio", False, "radio stalled", "")
                self.alert("heal_failed", False, "radio problem, manual action needed", "")
        # spots
        if st["no_spots_hours"] > 0:
            with self.db.lock:
                last = self.db.con.execute("SELECT COALESCE(MAX(ts),0) FROM spots").fetchone()[0]
            gap = now - max(last, self.started)
            self.alert("no_spots", gap > st["no_spots_hours"] * 3600, "no spots", f"No station decoded for {dur(gap)} while the radio works (antenna? band?).", False)
        # disk / database
        try:
            free = shutil.disk_usage(self.cfg["data_dir"]).free / 1e6
            self.alert("disk", free < st["disk_min_mb"], "low disk space", f"Only {free:.0f} MB free in the data directory.")
            dbp = os.path.join(self.cfg["data_dir"], "wsm.sqlite")
            size = sum(os.path.getsize(p) for p in (dbp, dbp + "-wal") if os.path.exists(p)) / 1e6
            self.alert("db_size", size > st["db_max_mb"], "database is big", f"wsm.sqlite is {size:.0f} MB; lower keep_days in wsm.yaml.", False)
        except OSError:
            pass
        # computer: power, temperature, sample loss (wsm_sys.SysMon, attached to the shared state by wsm_service)
        sm = getattr(self.state, "sysmon", None)
        if sm is not None:
            self.check_system(sm.snapshot())
        # clock (every 10 min)
        if now - self.last_clock > 600:
            self.last_clock = now
            off = self.offset_fn()
            self.clock_bad = abs(off) > st["clock_max_s"] if off is not None else (self.synced_fn() is False)
            msg = f"System clock is off by {off:+.1f} s (WSPR needs < 1 s)." if off is not None else "NTP is not synchronized."
            self.alert("clock", self.clock_bad, "system clock is off", msg)
        # heartbeat
        if st["heartbeat_url"] and now - self.last_beat >= st["heartbeat_min"] * 60:
            self.last_beat = now
            url = st["heartbeat_url"] + ("/fail" if "radio" in self.h["alerts"] or "heal_failed" in self.h["alerts"] else "")
            try:
                urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=15).read(100)
            except Exception as e:
                self.log(f"health: heartbeat failed ({type(e).__name__})")

    def run(self):
        self.log("health: monitor on (self-heal " + ("on" if self.st["self_heal"] else "off") + ", ntfy " + ("configured" if self.st["ntfy_topic"] else "NOT configured") + ")")
        while not self.stop.wait(self.st["check_s"]):
            try:
                self.step()
            except Exception as e:
                self.log(f"health: check failed ({type(e).__name__}: {e})")


def start(cfg, db, state, stop, log=print):
    if not settings(cfg)["enabled"]:
        log("health: monitor is off (health.enabled: no)")
        return None
    th = threading.Thread(target=Monitor(cfg, db, state, stop, log).run, name="health", daemon=True)
    th.start()
    return th


def main():
    ap = argparse.ArgumentParser(description="WSM health monitor")
    ap.add_argument("-c", "--config", default=os.path.expanduser("~/wsm/wsm.yaml"))
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--test", action="store_true", help="send a test notification")
    g.add_argument("--status", action="store_true", help="show the settings and the active alerts")
    a = ap.parse_args()
    cfg = wsm_config.load(a.config if os.path.exists(os.path.expanduser(a.config)) else None)
    st = settings(cfg)
    if a.status:
        try:
            h = json.load(open(os.path.join(cfg["data_dir"], "health.json")))
        except Exception:
            h = {}
        print(f"monitor: {'on' if st['enabled'] else 'off'}   self-heal: {'on' if st['self_heal'] else 'off'}   ntfy: {'configured' if st['ntfy_topic'] else 'NOT configured'}"
              f"   heartbeat: {'yes' if st['heartbeat_url'] else 'no'}")
        for k, v in (h.get("alerts") or {}).items():
            print(f"ACTIVE {k}: {v['title']} (since {time.strftime('%Y-%m-%d %H:%M', time.localtime(v['since']))})")
        print(f"self-heal restarts in the last {st['window_min']:.0f} min: {len([t for t in h.get('restarts', []) if time.time() - t < st['window_min'] * 60])}")
        return
    if not st["ntfy_topic"]:
        raise SystemExit("set health.ntfy_topic (or daily_report.ntfy_topic) in wsm.yaml first")
    ntfy(st, "WSM: health test", "Health alerts work. You will be told when the radio stalls, WSM restarts itself, or the disk/clock misbehave.", 3, ("white_check_mark",))
    print("test notification sent")


if __name__ == "__main__":
    main()
