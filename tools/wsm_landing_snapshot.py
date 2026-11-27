#!/usr/bin/env python3
"""Publish the station landing page without WSM running: a static copy that another web server (lighttpd) serves.

usage:  python3 tools/wsm_landing_snapshot.py [-c ~/wsm/wsm.yaml] [--out /var/www/station] [--online]

Writes into --out (default /var/www/station; create it once with
  sudo mkdir -p /var/www/station && sudo chown pi: /var/www/station ):
  station.html  the landing page (the same file WSM serves)
  favicon.svg
  landing.json  the texts of the page (title, about, equipment, links, projects) and the WSPR records from the database,
                marked "wsm_offline" so the page says that the WSPR monitor is temporarily unavailable
                (--online leaves that mark out)
The page asks /api/landing first (WSM); when that fails it reads landing.json and shows the ADS-B numbers live from the
readsb files that tar1090 publishes (/tar1090/data/aircraft.json). Run it again after changing the landing texts.
Only what the page already shows in public goes into landing.json: no exact position, no keys, no feeder ids.
"""
import argparse
import json
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import wsm_config   # noqa: E402
import wsm_db       # noqa: E402
import wsm_landing  # noqa: E402
import wsm_web      # noqa: E402

HERE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")


def snapshot(cfg, db, online=False, now=None):
    """the landing.json data: texts and WSPR records, no live numbers (WSM is not running)"""
    d = dict(wsm_landing.landing(cfg, db, None))
    w = dict(d.get("wspr") or {})
    d["wspr"] = dict(best_all=w.get("best_all"), spots_24h=None, stations_24h=None, best_24h=None, bands_24h=[], listening=[])
    d["adsb"] = dict(ok=False)                       # the page reads the live ADS-B numbers itself
    d["wsm_offline"] = not online
    d["snapshot"] = int(now or time.time())
    return d


def _write(path, data, mode=0o644):
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".tmp_")
    with os.fdopen(fd, "wb") as f:
        f.write(data if isinstance(data, bytes) else data.encode("utf-8"))
    os.chmod(tmp, mode)
    os.replace(tmp, path)


def publish(cfg, out, online=False):
    os.makedirs(out, exist_ok=True)
    db = wsm_db.DB(os.path.join(cfg["data_dir"], "wsm.sqlite"), cfg["station"]["grid"])
    data = snapshot(cfg, db, online)
    with open(os.path.join(HERE, "static", "station.html"), "rb") as f:
        _write(os.path.join(out, "station.html"), f.read())
    _write(os.path.join(out, "favicon.svg"), wsm_web.FAVICON_SVG)
    _write(os.path.join(out, "landing.json"), json.dumps(data, separators=(",", ":")))
    return data


def main():
    ap = argparse.ArgumentParser(description="static copy of the station landing page (WSM not running)")
    ap.add_argument("-c", "--config", default="~/wsm/wsm.yaml")
    ap.add_argument("--out", default="/var/www/station")
    ap.add_argument("--online", action="store_true", help="do not mark the WSPR monitor as unavailable")
    a = ap.parse_args()
    cfg = wsm_config.load(os.path.expanduser(a.config))
    d = publish(cfg, a.out, a.online)
    b = (d["wspr"] or {}).get("best_all") or {}
    print(f"written to {a.out}: station.html, favicon.svg, landing.json  ({d['station']['callsign']}, "
          f"WSPR {'online' if a.online else 'marked temporarily unavailable'}, best DX ever {b.get('dist_km', '-')} km)")


if __name__ == "__main__":
    main()
