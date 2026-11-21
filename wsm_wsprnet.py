#!/usr/bin/env python3
"""wsm_wsprnet.py - upload the decoded spots to WSPRnet (wsprnet.org), controlled by `wsprnet.upload: yes|no` in wsm.yaml.

Each spot is reported with the same HTTP request that WSJT-X / rtlsdr-wsprd use (GET http://wsprnet.org/post?function=wspr&...).
Nothing is sent unless `upload` is yes. When it is switched on, only spots decoded from then on are uploaded (no old backlog),
and spots older than `max_age_h` are never sent (after a long outage they would only be stale).

wsm.yaml:
  wsprnet:
    upload: no            # yes / no - the master switch (default: no)
    rcall: ""             # reporter callsign, default: station.callsign
    rgrid: ""             # reporter locator, default: 6 characters computed from station.lat/lon (else station.grid)
    upload_own_tx: no     # yes: also report your own transmitter (rcall == tcall); normally pointless, useful for a first test
    max_age_h: 12         # never upload spots older than this
    url: http://wsprnet.org/post

Test:  python3 wsm_wsprnet.py --dry-run    list the spots of the last hours and the requests that would be sent (sends nothing)
       python3 wsm_wsprnet.py --status     show whether the upload is on and the last uploaded spot
"""
import argparse
import json
import os
import random
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

import wsm_config
import wsm_geo

VERSION = "WSM 1.0"
UA = "wsm-wspr-system-monitor/1.0"
DEFAULTS = dict(
    upload=False,
    rcall="",
    rgrid="",
    upload_own_tx=False,
    max_age_h=12,
    url="http://wsprnet.org/post",
    poll_s=20,                       # how often the database is checked for new spots
)


def _bool(v):
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "yes", "y", "on", "sim", "s")
    return bool(v)


def settings(cfg):
    s = dict(DEFAULTS)
    s.update(cfg.get("wsprnet") or {})
    st = cfg["station"]
    s["upload"] = _bool(s["upload"])
    s["upload_own_tx"] = _bool(s["upload_own_tx"])
    s["rcall"] = (str(s["rcall"]).strip() or str(st["callsign"])).upper()
    s["rgrid"] = str(s["rgrid"]).strip() or grid6(st.get("lat"), st.get("lon")) or str(st["grid"])
    s["max_age_h"] = float(s["max_age_h"])
    return s


def grid6(lat, lon):
    """6-character Maidenhead locator of a position, or None."""
    try:
        lat, lon = float(lat), float(lon)
    except (TypeError, ValueError):
        return None
    if not (-90 <= lat < 90 and -180 <= lon < 180):
        return None
    a, o = lat + 90.0, lon + 180.0
    return (chr(65 + int(o // 20)) + chr(65 + int(a // 10)) + str(int(o % 20 // 2)) + str(int(a % 10 // 1))
            + chr(97 + int(o % 2 // (2 / 24))) + chr(97 + int(a % 1 // (1 / 24))))


def usable(sp, st):
    """WSPRnet needs a plain callsign and a locator; hashed or incomplete messages are not reported."""
    call, grid = (sp.get("call") or ""), (sp.get("grid") or "")
    if not call or "<" in call or "..." in call or not re.fullmatch(r"[A-Z0-9/]{3,12}", call.upper()):
        return False
    if wsm_geo.grid_to_latlon(grid) is None or sp.get("power_dbm") is None or sp.get("snr") is None:
        return False
    if not st["upload_own_tx"] and call.upper() == st["rcall"]:
        return False
    return True


def params(sp, st):
    """query parameters of one spot (the date and time are those of the start of the 2-minute slot, UTC)"""
    t = time.gmtime(sp["ts"])
    return dict(
        function="wspr", rcall=st["rcall"], rgrid=st["rgrid"],
        rqrg=f"{wsm_config.BANDS[sp['band']] / 1e6:.6f}",           # dial frequency of the receiver, MHz
        date=time.strftime("%y%m%d", t), time=time.strftime("%H%M", t),
        sig=int(sp["snr"]), dt=f"{float(sp['dt'] or 0.0):.1f}", drift=int(sp["drift"] or 0),
        tqrg=f"{sp['freq_hz'] / 1e6:.6f}",                          # RF frequency of the transmitter, MHz
        tcall=sp["call"].upper(), tgrid=sp["grid"], dbm=int(sp["power_dbm"]),
        version=VERSION, mode=2)                                      # mode 2 = WSPR-2 (2-minute slots)


def post(st, sp):
    """-> the status lines of the reply (those that mention spots or errors). Raises on network or HTTP errors."""
    url = st["url"] + "?" + urllib.parse.urlencode(params(sp, st))
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=30) as r:
        if r.status != 200:
            raise RuntimeError(f"HTTP {r.status}")
        text = r.read(20000).decode("utf-8", "replace")
    lines = [re.sub(r"<[^>]+>", "", ln).strip() for ln in text.splitlines()]
    return [ln for ln in lines if re.search(r"spot|error|fail|invalid|duplicate|added", ln, re.I)][:3] or ["(no status line in the reply)"]


# ---------- state: id of the last spot that was handled ----------
def _state_path(cfg):
    return os.path.join(cfg["data_dir"], "wsprnet.json")


def _load(cfg):
    try:
        return json.load(open(_state_path(cfg)))
    except Exception:
        return {}


def _save(cfg, d):
    tmp = _state_path(cfg) + ".tmp"
    with open(tmp, "w") as f:
        json.dump(d, f)
    os.replace(tmp, _state_path(cfg))


def _max_id(db):
    with db.lock:
        return db.con.execute("SELECT COALESCE(MAX(id), 0) FROM spots").fetchone()[0]


def pending(db, st, last_id, now=None):
    now = now or time.time()
    rows = db.spots(now - st["max_age_h"] * 3600, None, None, 1000000)
    return sorted((r for r in rows if r["id"] > last_id), key=lambda r: r["id"])


def run_forever(cfg, db, stop, log=print):
    st = settings(cfg)
    state = _load(cfg)
    if "last_id" not in state:                                       # first start with the upload on: no backlog
        state["last_id"] = _max_id(db)
        _save(cfg, state)
        log(f"wsprnet: upload on as {st['rcall']} {st['rgrid']}; only new spots are sent")
    else:
        log(f"wsprnet: upload on as {st['rcall']} {st['rgrid']}")
    replies_logged = 0
    fails = 0
    while not stop.wait(st["poll_s"] if not fails else min(300, 20 * 2 ** min(fails, 4))):
        todo = pending(db, st, state["last_id"])
        if not todo:
            continue
        stop.wait(random.uniform(0, 15))                             # spread the load on the server, like WSJT-X does
        sent = skipped = 0
        for sp in todo:
            if stop.is_set():
                break
            if usable(sp, st):
                try:
                    reply = post(st, sp)
                except Exception as e:
                    fails += 1
                    log(f"wsprnet: upload failed ({type(e).__name__}: {e}); will retry")
                    break
                sent += 1
                if replies_logged < 3:
                    replies_logged += 1
                    log(f"wsprnet: server reply: {' | '.join(reply)[:160]!r}")
                time.sleep(0.5)
            else:
                skipped += 1
            state["last_id"] = sp["id"]
            fails = 0
        _save(cfg, state)
        if sent or skipped:
            log(f"wsprnet: {sent} spot(s) uploaded" + (f", {skipped} not reportable" if skipped else ""))


def start(cfg, db, stop, log=print):
    st = settings(cfg)
    if not st["upload"]:
        log("wsprnet: upload is off (wsprnet.upload: no)")
        return None
    th = threading.Thread(target=run_forever, args=(cfg, db, stop, log), name="wsprnet", daemon=True)
    th.start()
    return th


def main():
    import wsm_db
    ap = argparse.ArgumentParser(description="WSM upload to WSPRnet")
    ap.add_argument("-c", "--config", default=os.path.expanduser("~/wsm/wsm.yaml"))
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry-run", action="store_true", help="show the spots and requests that would be sent; sends nothing")
    g.add_argument("--status", action="store_true", help="show the upload settings and state")
    a = ap.parse_args()
    cfg = wsm_config.load(a.config if os.path.exists(os.path.expanduser(a.config)) else None)
    st = settings(cfg)
    s = cfg["station"]
    ll = (float(s["lat"]), float(s["lon"])) if s.get("lat") is not None and s.get("lon") is not None else None
    db = wsm_db.DB(os.path.join(cfg["data_dir"], "wsm.sqlite"), s["grid"], ll)
    state = _load(cfg)
    print(f"upload: {'ON' if st['upload'] else 'off'}   reporter: {st['rcall']} {st['rgrid']}   own TX: {'yes' if st['upload_own_tx'] else 'no'}"
          f"   last spot id handled: {state.get('last_id', '(not started yet)')}")
    if a.status:
        return
    rows = db.spots(time.time() - st["max_age_h"] * 3600, None, None, 1000000)
    n_ok = 0
    for sp in sorted(rows, key=lambda r: r["id"]):
        ok = usable(sp, st)
        n_ok += ok
        print(("SEND " if ok else "skip ") + f"{sp['call']:<8} {sp['grid']:<4} {sp['band']:<4} {time.strftime('%H:%M', time.gmtime(sp['ts']))}Z "
              f"snr {sp['snr']:>3}" + (("  " + urllib.parse.urlencode(params(sp, st))) if ok else ""))
    print(f"{n_ok} of {len(rows)} spots of the last {st['max_age_h']:g} h would be reportable (the service only sends spots decoded after it started)")


if __name__ == "__main__":
    main()
