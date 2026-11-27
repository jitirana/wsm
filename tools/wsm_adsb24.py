#!/usr/bin/env python3
"""How many different aircraft the ADS-B receiver saw in the last 24 hours, for the station landing page.

usage:  python3 tools/wsm_adsb24.py [--json-dir /run/readsb] [--state ~/wsm/data/adsb24_state.json]
                                     [--out /var/www/station/adsb24.json]
Run once a minute (deploy/wsm-adsb24.timer). Each run reads readsb's aircraft.json, remembers when every aircraft
(ICAO hex address) was last heard, forgets the ones older than 24 h, and writes
  {"aircraft_24h": N, "window_h": hours covered (< 24 right after the first start), "updated": unix time}
The state file keeps the hex addresses only (the same public data the aircraft broadcast); nothing about the station.
"""
import argparse
import json
import os
import tempfile
import time

DAY = 86400


def update(state, aircraft_json, now=None):
    """state: {"first": ts, "seen": {hex: ts}} (changed in place) -> (count, window_h)"""
    now = float(now if now is not None else time.time())
    seen = state.setdefault("seen", {})
    state.setdefault("first", now)
    if aircraft_json and now - float(aircraft_json.get("now", 0)) < 120:      # readsb is running
        for a in aircraft_json.get("aircraft", []):
            h = str(a.get("hex") or "").lower()
            if h and not h.startswith("~") and float(a.get("seen", 999)) < 60:  # "~": non-ICAO (TIS-B) ids, not aircraft
                seen[h] = now
    for h in [h for h, t in seen.items() if now - t > DAY]:
        del seen[h]
    window = min(DAY, now - float(state["first"])) / 3600.0
    return len(seen), round(window, 1)


def _read(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _write(path, obj, mode=0o644):
    d = os.path.dirname(path) or "."
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".tmp_")
    with os.fdopen(fd, "w") as f:
        json.dump(obj, f, separators=(",", ":"))
    os.chmod(tmp, mode)
    os.replace(tmp, path)


def main():
    ap = argparse.ArgumentParser(description="aircraft seen in the last 24 h (for the landing page)")
    ap.add_argument("--json-dir", default="/run/readsb")
    ap.add_argument("--state", default="~/wsm/data/adsb24_state.json")
    ap.add_argument("--out", default="/var/www/station/adsb24.json")
    a = ap.parse_args()
    state_path = os.path.expanduser(a.state)
    os.makedirs(os.path.dirname(state_path), exist_ok=True)
    state = _read(state_path) or {}
    now = time.time()
    n, window = update(state, _read(os.path.join(a.json_dir, "aircraft.json")), now)
    _write(state_path, state, 0o600)
    _write(os.path.expanduser(a.out), dict(aircraft_24h=n, window_h=window, updated=int(now)))


if __name__ == "__main__":
    main()
