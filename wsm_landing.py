"""
wsm_landing.py - the data of the station landing page (a public page, e.g. https://example.org/).

WSPR numbers come from the WSM database; ADS-B numbers from the JSON files that readsb writes (aircraft.json, receiver.json).
Only public, coarse information leaves this module: counts, distances and the 4-character locator, never the exact position
of the station or any feeder ID. The result is cached for CACHE_S seconds so a busy page cannot load the Raspberry Pi.
"""
import json
import os
import threading
import time

import wsm_geo

CACHE_S = 30
_lock = threading.Lock()
_cache = {"t": 0.0, "data": None}
_rate = {"t": None, "n": None, "value": None}       # message counter of readsb between two reads -> messages per second


def _read_json(path):
    try:
        with open(path, "rb") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def adsb(json_dir):
    """-> dict(ok, aircraft, with_pos, msg_rate, farthest_km, farthest) from readsb, or dict(ok=False)."""
    ac = _read_json(os.path.join(json_dir, "aircraft.json"))
    if not ac or time.time() - float(ac.get("now", 0)) > 120:       # readsb stopped: the file is old
        return dict(ok=False)
    rx = _read_json(os.path.join(json_dir, "receiver.json")) or {}
    rlat, rlon = rx.get("lat"), rx.get("lon")
    seen = [a for a in ac.get("aircraft", []) if float(a.get("seen", 999)) < 60]
    pos = [a for a in seen if a.get("lat") is not None and float(a.get("seen_pos", 999)) < 60]
    far, far_km = None, None
    for a in pos:
        if a.get("r_dst") is not None:
            km = float(a["r_dst"]) * 1.852                           # readsb gives nautical miles
        elif rlat is not None and rlon is not None:
            km = wsm_geo.distance_bearing(float(rlat), float(rlon), float(a["lat"]), float(a["lon"]))[0]
        else:
            continue
        if far_km is None or km > far_km:
            far_km, far = km, a
    now, n = float(ac.get("now", 0)), ac.get("messages")
    if isinstance(n, (int, float)):
        if _rate["t"] is not None and now > _rate["t"] and n >= _rate["n"]:
            _rate["value"] = (n - _rate["n"]) / (now - _rate["t"])
        _rate["t"], _rate["n"] = now, n
    st = _read_json(os.path.join(json_dir, "stats.json")) or {}
    m1 = (st.get("last1min") or {}).get("messages")
    if isinstance(m1, (int, float)) and m1 > 0:                       # readsb: messages in the last minute
        _rate["value"] = m1 / 60.0
    out = dict(ok=True, aircraft=len(seen), with_pos=len(pos),
               msg_rate=None if _rate["value"] is None else round(_rate["value"], 1),
               farthest_km=None if far_km is None else round(far_km))
    if far:
        alt = far.get("alt_baro")
        out["farthest"] = dict(flight=(far.get("flight") or "").strip() or None,
                               alt_ft=alt if isinstance(alt, (int, float)) else None)
    return out


def _spot(s):
    if not s:
        return None
    return dict(call=s.get("call"), grid=s.get("grid"), band=s.get("band"), snr=s.get("snr"), ts=s.get("ts"),
                dist_km=None if s.get("dist_km") is None else round(s["dist_km"]))


def landing(cfg, db, state):
    """-> the JSON of the landing page (cached)."""
    with _lock:
        if _cache["data"] is not None and time.time() - _cache["t"] < CACHE_S:
            return _cache["data"]
        L, st = cfg.get("landing") or {}, cfg["station"]
        own = st["callsign"]
        sm = db.summary(time.time() - 24 * 3600, own, hide_own=True)
        radios = state.snapshot() if state is not None else []
        data = dict(
            station=dict(callsign=own, grid=str(st["grid"])[:4]),
            title=L.get("title") or f"{own} radio station",
            about=L.get("about") or "", equipment=list(L.get("equipment") or []),
            links=list(L.get("links") or []), projects=list(L.get("projects") or []),
            monitor_url=L.get("monitor_url") or "", adsb_url=L.get("adsb_url") or "",
            wspr=dict(spots_24h=sm.get("spots"), stations_24h=sm.get("calls"), best_24h=_spot(sm.get("best")),
                      best_all=_spot(sm.get("best_all")),
                      bands_24h=[dict(band=b["band"], spots=b["spots"], calls=b["calls"]) for b in sm.get("bands", [])],
                      listening=[dict(name=r.get("name"), state=r.get("state"), band=r.get("band")) for r in radios]),
            adsb=adsb((cfg.get("adsb") or {}).get("json_dir") or "/run/readsb"),
            now=int(time.time()))
        _cache.update(t=time.time(), data=data)
        return data
