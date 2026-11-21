#!/usr/bin/env python3
"""wsm_report.py - one daily ntfy notification with the farthest spot heard since the previous notification.

The place is shown as city and country (reverse geocoded from the centre of the 4-character locator square, so it is the
place nearest to that point, +-100 km), never as the locator. Your own callsign counts like any other station (list it in exclude_calls to leave it out). The ntfy topic is a secret: it
lives only in wsm.yaml (daily_report.ntfy_topic) and is never logged.

Test:  python3 wsm_report.py --demo       send a sample notification (clearly marked DEMO)
       python3 wsm_report.py --dry-run    build the real report from the database and print it
       python3 wsm_report.py --test       build the real report and send it now
"""
import argparse
import datetime as dt
import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

import wsm_geo

DEFAULTS = dict(
    enabled=True,
    time="17:00:00",                  # local time, HH:MM or HH:MM:SS
    timezone="America/Fortaleza",
    ntfy_topic="",                    # topic name or full URL (https://ntfy.sh/NAME). Keep it private.
    ntfy_server="https://ntfy.sh",
    exclude_calls=[],                 # callsigns to ignore (default: none; your own TX counts like any other)
    send_when_empty=True,             # send a short "nothing heard" message when no station was heard
    geocoder="nominatim",             # "nominatim" (OpenStreetMap, needs internet) or "none"
    language="en",                    # language of the place names
    grace_min=30,                     # if the service was down at the time, still send within this many minutes
)
UA = "wsm-wspr-system-monitor/1.0"


def settings(cfg):
    s = dict(DEFAULTS)
    s.update(cfg.get("daily_report") or {})
    topic, server = str(s["ntfy_topic"] or "").strip(), str(s["ntfy_server"]).rstrip("/")
    if "/" in topic:                                      # accept "ntfy.sh/NAME" or "https://ntfy.sh/NAME"
        t = topic if "://" in topic else "https://" + topic
        p = urllib.parse.urlparse(t)
        server, topic = f"{p.scheme}://{p.netloc}", p.path.strip("/")
    s["ntfy_topic"], s["ntfy_server"] = topic, server
    return s


def tzinfo(name):
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(name)
    except Exception:
        return dt.timezone(dt.timedelta(hours=-3))      # Fortaleza has no daylight saving time


def parse_hms(text):
    p = [int(x) for x in str(text).split(":")] + [0, 0]
    return p[0], p[1], p[2]


def run_epoch(day, hms, tz):
    h, m, sec = hms
    return dt.datetime(day.year, day.month, day.day, h, m, sec, tzinfo=tz).timestamp()


def next_run(now, hms, tz):
    """epoch of the next local HH:MM:SS strictly after `now`"""
    today = dt.datetime.fromtimestamp(now, tz).date()
    t = run_epoch(today, hms, tz)
    return t if t > now else run_epoch(today + dt.timedelta(days=1), hms, tz)


# ---------- place names ----------
def geocode(lat, lon, grid, st, data_dir):
    """-> 'City, Country'. Cached per locator in data_dir/geocache.json; falls back to a plain description."""
    path = os.path.join(data_dir, "geocache.json")
    try:
        cache = json.load(open(path))
    except Exception:
        cache = {}
    key = f"{grid}|{st['language']}"
    if key in cache:
        return cache[key]
    place = None
    if st["geocoder"] == "nominatim":
        url = "https://nominatim.openstreetmap.org/reverse?" + urllib.parse.urlencode(
            dict(format="jsonv2", lat=f"{lat:.4f}", lon=f"{lon:.4f}", zoom=10, addressdetails=1, **{"accept-language": st["language"]}))
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=15) as r:
                d = json.load(r)
            a = d.get("address") or {}
            town = next((a[k] for k in ("city", "town", "village", "municipality", "county", "state_district", "state", "region") if a.get(k)), None)
            country = a.get("country")
            if town and country:
                place = f"{town}, {country}"
            elif country:
                place = country
        except Exception:
            place = None
        time.sleep(1.1)                                    # be polite to the free service
    if place:
        cache[key] = place
        try:
            json.dump(cache, open(path, "w"), ensure_ascii=False)
        except OSError:
            pass
        return place
    return "open sea or unknown place"                     # not cached: try again next time


# ---------- report ----------
def build(cfg, db, st, since_ts, until_ts):
    own = {str(c).upper() for c in st["exclude_calls"]}
    rows = [r for r in db.spots(since_ts, None, None, 1000000)
            if r["ts"] <= until_ts and r["call"].upper() not in own and r["dist_km"] is not None]
    if not rows:
        return None
    best = max(rows, key=lambda r: (r["dist_km"], r["snr"]))
    return dict(best=best, spots=len(rows), calls=len({r["call"] for r in rows}), since=since_ts, until=until_ts)


def compose(rep, place, tz, cfg, demo=False):
    tag = "DEMO - " if demo else ""
    if rep is None:
        return f"{tag}WSM: no station heard", "No station was heard since the last notification.", None
    b = rep["best"]
    loc = dt.datetime.fromtimestamp(b["ts"], tz)
    title = f"{tag}WSM farthest: {b['call']}, {b['dist_km']:,.0f} km"
    body = (f"{b['call']} - {place}\n"
            f"{b['band']} - {b['freq_hz'] / 1e6:.6f} MHz - SNR {b['snr']} dB\n"
            f"{b['dist_km']:,.0f} km, bearing {b['bearing']:.0f} deg\n"
            f"Heard {loc:%Y-%m-%d %H:%M} local\n\n"
            f"Since the last notification: {rep['spots']} spots from {rep['calls']} stations")
    return title, body, b.get("map")


def send(st, title, message, click=None):
    """ntfy JSON publish (UTF-8 safe). The topic is in the request body, never in a log."""
    msg = dict(topic=st["ntfy_topic"], title=title, message=message, tags=["satellite_antenna"], priority=3)
    if click:
        msg["click"] = click
    req = urllib.request.Request(st["ntfy_server"] + "/", data=json.dumps(msg).encode("utf-8"),
                                 headers={"Content-Type": "application/json", "User-Agent": UA})
    with urllib.request.urlopen(req, timeout=20) as r:
        if r.status // 100 != 2:
            raise RuntimeError(f"ntfy answered {r.status}")


# ---------- scheduler ----------
def _state_path(cfg):
    return os.path.join(cfg["data_dir"], "daily_report.json")


def _load_state(cfg):
    try:
        return json.load(open(_state_path(cfg)))
    except Exception:
        return {}


def _save_state(cfg, last_ts):
    with open(_state_path(cfg), "w") as f:
        json.dump(dict(last_ts=last_ts), f)


def prepare(cfg, db, st, tz, since, until):
    rep = build(cfg, db, st, since, until)
    if rep is None:
        return None, None
    b = rep["best"]
    return rep, geocode(b["lat"], b["lon"], b["grid"], st, cfg["data_dir"])


def run_forever(cfg, db, stop, log=print):
    st = settings(cfg)
    tz, hms = tzinfo(st["timezone"]), parse_hms(st["time"])
    log(f"daily report: {st['time']} {st['timezone']} (ntfy topic configured)")
    while not stop.is_set():
        now = time.time()
        last = _load_state(cfg).get("last_ts")
        today = dt.datetime.fromtimestamp(now, tz).date()
        t_today = run_epoch(today, hms, tz)
        if now >= t_today and now - t_today <= st["grace_min"] * 60 and (last is None or last < t_today):
            target = t_today                                 # missed by a little (restart): send now
        else:
            target = next_run(now, hms, tz)
        since = (last + 1) if last else target - 86400
        while not stop.is_set() and time.time() < target - 90:      # sleep until 90 s before
            time.sleep(min(5.0, max(0.1, target - 90 - time.time())))
        if stop.is_set():
            return
        try:
            rep, place = prepare(cfg, db, st, tz, since, target)  # geocode early so the message leaves exactly on time
        except Exception as e:
            log(f"daily report: could not prepare ({type(e).__name__}: {e})")
            rep, place = None, None
        while not stop.is_set() and time.time() < target:
            time.sleep(min(0.25, max(0.01, target - time.time())))
        if stop.is_set():
            return
        if rep is None and not st["send_when_empty"]:
            _save_state(cfg, target)
            log("daily report: nothing to report, no notification")
            continue
        title, body, click = compose(rep, place, tz, cfg)
        ok = False
        for attempt in range(4):
            try:
                send(st, title, body, click)
                ok = True
                break
            except Exception as e:
                log(f"daily report: send failed ({type(e).__name__}), attempt {attempt + 1}/4")
                if stop.wait(20):
                    return
        if ok:
            _save_state(cfg, target)
            log("daily report: sent" + (f" ({rep['best']['call']}, {rep['best']['dist_km']:.0f} km)" if rep else " (nothing heard)"))
        else:
            _save_state(cfg, target)                           # do not repeat old news tomorrow: next window starts here
            log("daily report: gave up for today")


def start(cfg, db, stop, log=print):
    st = settings(cfg)
    if not st["enabled"]:
        return None
    if not st["ntfy_topic"]:
        log("daily report: no ntfy_topic in wsm.yaml, notification disabled")
        return None
    th = threading.Thread(target=run_forever, args=(cfg, db, stop, log), name="daily-report", daemon=True)
    th.start()
    return th


def main():
    import wsm_config
    import wsm_db
    ap = argparse.ArgumentParser(description="WSM daily ntfy report")
    ap.add_argument("-c", "--config", default=os.path.expanduser("~/wsm/wsm.yaml"))
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--demo", action="store_true", help="send a sample notification (marked DEMO)")
    g.add_argument("--dry-run", action="store_true", help="build the real report and print it")
    g.add_argument("--test", action="store_true", help="build the real report and send it now")
    a = ap.parse_args()
    cfg = wsm_config.load(a.config if os.path.exists(os.path.expanduser(a.config)) else None)
    st = settings(cfg)
    tz = tzinfo(st["timezone"])
    s = cfg["station"]
    ll = (float(s["lat"]), float(s["lon"])) if s.get("lat") is not None and s.get("lon") is not None else wsm_geo.grid_to_latlon(s["grid"])
    now = time.time()
    if a.demo:
        lat, lon = wsm_geo.grid_to_latlon("PM95")
        d, brg = wsm_geo.distance_bearing(ll[0], ll[1], lat, lon)
        best = dict(call="DEMO", band="20m", freq_hz=14097100.0, snr=-21, dist_km=d, bearing=brg, ts=now, grid="PM95", lat=lat, lon=lon,
                    map=wsm_geo.maps_url(lat, lon))
        rep = dict(best=best, spots=12, calls=5)
        place = geocode(lat, lon, "PM95", st, cfg["data_dir"])
    else:
        db = wsm_db.DB(os.path.join(cfg["data_dir"], "wsm.sqlite"), s["grid"], ll)
        last = _load_state(cfg).get("last_ts")
        rep, place = prepare(cfg, db, st, tz, (last + 1) if last else now - 86400, now)
    title, body, click = compose(rep, place, tz, cfg, demo=a.demo)
    print(title + "\n" + body + ("\n" + click if click else ""))
    if a.dry_run:
        return
    if not st["ntfy_topic"]:
        raise SystemExit("set daily_report.ntfy_topic in wsm.yaml first")
    send(st, title, body, click)
    print("sent")


if __name__ == "__main__":
    main()
