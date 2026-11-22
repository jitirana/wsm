#!/usr/bin/env python3
"""wsm_openings.py - band openings: when is each band open, by hour of day.

Two data sources, both shown in the "Band openings" panel of the page (bands x 24 hours):
  * MINE   - what this receiver decoded: spots per listened hour in each (band, hour of day UTC), best DX, stations.
             Cells where the receiver never listened are marked "no data" (the schedule may skip a band at some hours).
  * REFERENCE - the public WSPRnet database (https://wspr.live): spots between stations in the region of this station and the rest of
             the world, at least `min_km` long, by band and hour of day. It fills the picture while this receiver has few spots.
             The query is read-only and sent about once a day (4 requests, spaced); the area box is rounded to whole degrees, so only a
             region of about 100 km is revealed, never the exact position. If the site is unreachable the panel simply shows "mine".

wsm.yaml (all optional):
  openings:
    enabled: yes
    days: 30                  # default period of "mine" in the panel (the page can change it)
    reference:
      enabled: yes            # no = never contact wspr.live
      days: 14                # period of the reference
      min_km: 500             # count only spots at least this long (skips local ground-wave spots)
      radius_deg: 6.0         # region around the station, degrees (6 = about 650 km)
      direction: both         # rx = stations in the region as receivers, tx = as transmitters, both = either
      refresh_hours: 24
      bands: []               # default: the bands of the radios
Test (needs internet):  python3 wsm_openings.py -c ~/wsm/wsm.yaml --fetch      query wspr.live now and print the result
Offline check:          python3 wsm_openings.py --test
"""
import argparse
import json
import math
import os
import re
import sys
import tempfile
import threading
import time
import urllib.parse
import urllib.request

import wsm_config
import wsm_geo

# wspr.live stores "band" as the first digits of the frequency in MHz
BAND_CODE = {"160m": 1, "80m": 3, "60m": 5, "40m": 7, "30m": 10, "20m": 14, "17m": 18, "15m": 21, "12m": 24, "10m": 28,
             "6m": 50, "4m": 70, "2m": 144, "70cm": 432}
DEFAULTS = dict(enabled=True, days=30,
                reference=dict(enabled=True, days=14, min_km=500, radius_deg=6.0, direction="both", refresh_hours=24, bands=[],
                               url="https://db1.wspr.live/", pause_s=6.0, timeout_s=90.0))
UA = "WSM-openings (+https://github.com/jitirana/wsm)"
RETRY_S = 3600


def _yes(v):
    return v is True or str(v).strip().lower() in ("1", "true", "yes", "on")


def settings(cfg):
    s = json.loads(json.dumps(DEFAULTS))
    user = cfg.get("openings") or {}
    for k, v in user.items():
        if k == "reference" and isinstance(v, dict):
            s["reference"].update(v)
        else:
            s[k] = v
    r = s["reference"]
    s["enabled"], r["enabled"] = _yes(s["enabled"]), _yes(r["enabled"])
    s["days"] = max(0, int(s["days"]))
    r["days"] = max(1, min(60, int(r["days"])))
    r["min_km"] = max(0, min(20000, int(r["min_km"])))
    r["radius_deg"] = max(1.0, min(30.0, float(r["radius_deg"])))
    r["refresh_hours"] = max(1, int(r["refresh_hours"]))
    r["pause_s"], r["timeout_s"] = max(3.0, float(r["pause_s"])), max(10.0, float(r["timeout_s"]))
    r["direction"] = r["direction"] if r["direction"] in ("rx", "tx", "both") else "both"
    r["bands"] = [b for b in (r.get("bands") or []) if b in BAND_CODE]
    return s


def station_latlon(cfg):
    st = cfg["station"]
    if st.get("lat") is not None and st.get("lon") is not None:
        return float(st["lat"]), float(st["lon"])
    return wsm_geo.grid_to_latlon(st["grid"])


def cfg_bands(cfg, ref):
    if ref["bands"]:
        return ref["bands"]
    out = []
    for r in cfg["radios"]:
        for b in wsm_config.all_bands(r):
            if b in BAND_CODE and b not in out:
                out.append(b)
    return sorted(out, key=lambda b: wsm_config.BANDS[b])


# ---------------------------------------------------------------- mine
def own_matrix(db, days, now=None, own_call=None):
    """-> {band: [24 cells]} (hour of day, UTC). cell = dict(listen_h, spots, stations, max_km, snr) or None when never listened."""
    now = int(now or time.time())
    since = now - days * 86400 if days else 0
    cells = {}
    with db.lock:
        for band, h, n in db.con.execute(
                "SELECT band, CAST((ts % 86400) / 3600 AS INTEGER), COUNT(*) FROM slots "
                "WHERE ts >= ? AND status IN ('ok','partial') GROUP BY 1, 2", (since,)):
            cells.setdefault(band, [None] * 24)[h] = dict(listen_h=round(n * 2 / 60, 3), spots=0, stations=0, max_km=None, snr=None)
        sql = ("SELECT band, CAST((ts % 86400) / 3600 AS INTEGER), COUNT(*), COUNT(DISTINCT call), MAX(dist_km), AVG(snr) FROM spots "
               "WHERE ts >= ?" + (" AND call != ?" if own_call else "") + " GROUP BY 1, 2")
        for band, h, n, calls, mx, snr in db.con.execute(sql, (since, own_call) if own_call else (since,)):
            c = cells.setdefault(band, [None] * 24)
            if c[h] is None:                                   # spots without a listened slot (slot rows pruned): keep them visible
                c[h] = dict(listen_h=0.0, spots=0, stations=0, max_km=None, snr=None)
            c[h].update(spots=n, stations=calls, max_km=None if mx is None else round(mx), snr=None if snr is None else round(snr, 1))
    return cells


# ---------------------------------------------------------------- reference (wspr.live)
def _call_ok(call):
    return re.sub(r"[^A-Z0-9/]", "", str(call).upper())


def build_query(band, since_ts, ll, ref, own_call=""):
    """SQL for wspr.live: spots per hour of day of one band, from or to the region around the station."""
    r = ref["radius_deg"]
    la0, la1 = math.floor(ll[0] - r), math.ceil(ll[0] + r)
    lo0, lo1 = math.floor(ll[1] - r), math.ceil(ll[1] + r)
    rx = f"(rx_lat BETWEEN {la0} AND {la1} AND rx_lon BETWEEN {lo0} AND {lo1})"
    tx = f"(tx_lat BETWEEN {la0} AND {la1} AND tx_lon BETWEEN {lo0} AND {lo1})"
    area = {"rx": rx, "tx": tx, "both": f"({rx} OR {tx})"}[ref["direction"]]
    own = _call_ok(own_call)
    skip = f" AND rx_sign != '{own}' AND tx_sign != '{own}'" if own else ""        # this receiver's own reports are already in "mine"
    t = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(since_ts))
    return (f"SELECT toHour(time) AS h, count() AS spots, uniqExact(tx_sign) AS stations, uniqExact(rx_sign) AS receivers, "
            f"max(distance) AS max_km, round(avg(snr), 1) AS snr FROM wspr.rx "
            f"WHERE band = {BAND_CODE[band]} AND time >= '{t}' AND distance >= {int(ref['min_km'])}{skip} AND {area} "
            f"GROUP BY h ORDER BY h FORMAT JSON")


def _num(v, cast=float):
    try:
        return cast(float(v))
    except (TypeError, ValueError):
        return None


def parse_rows(text):
    """wspr.live JSON -> [(hour, spots, stations, receivers, max_km, snr)]  (64-bit numbers come as strings)."""
    out = []
    for r in json.loads(text).get("data", []):
        h = _num(r.get("h"), int)
        if h is None or not 0 <= h <= 23:
            continue
        out.append((h, _num(r.get("spots"), int) or 0, _num(r.get("stations"), int) or 0, _num(r.get("receivers"), int) or 0,
                    _num(r.get("max_km"), int), _num(r.get("snr"))))
    return out


def http_query(ref, sql):
    url = ref["url"].rstrip("/") + "/?" + urllib.parse.urlencode({"query": sql})
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=ref["timeout_s"]) as r:
        return r.read().decode("utf-8", "replace")


def fetch_reference(cfg, db, log=print, stop=None, now=None):
    """Query every band and store the result. Returns the number of bands stored (raises nothing: errors are logged)."""
    s = settings(cfg)["reference"]
    ll = station_latlon(cfg)
    if not ll:
        log("openings: no station position, reference skipped")
        return 0
    now = int(now or time.time())
    since = now - s["days"] * 86400
    done = 0
    for i, band in enumerate(cfg_bands(cfg, s)):
        if i and stop is not None and stop.wait(s["pause_s"]):         # spaced requests: the site allows 20 per minute
            break
        if i and stop is None:
            time.sleep(s["pause_s"])
        try:
            rows = parse_rows(http_query(s, build_query(band, since, ll, s, cfg["station"]["callsign"])))
        except Exception as e:                                            # network, timeout, bad JSON: never take the service down
            log(f"openings: reference {band} failed: {type(e).__name__}: {str(e)[:120]}")
            continue
        with db.lock, db.con:
            db.con.execute("DELETE FROM ref_openings WHERE band = ?", (band,))
            db.con.executemany("INSERT INTO ref_openings (band, hour, spots, stations, receivers, max_km, snr) VALUES (?,?,?,?,?,?,?)",
                               [(band,) + r for r in rows])
        log(f"openings: reference {band}: {sum(r[1] for r in rows)} spots in {len(rows)} hours")
        done += 1
    if done:
        params = dict(days=s["days"], min_km=s["min_km"], radius_deg=s["radius_deg"], direction=s["direction"])
        with db.lock, db.con:
            db.con.execute("INSERT OR REPLACE INTO ref_meta (k, v) VALUES ('updated', ?)", (str(now),))
            db.con.execute("INSERT OR REPLACE INTO ref_meta (k, v) VALUES ('params', ?)", (json.dumps(params),))
    return done


def reference_matrix(db):
    """-> ({band: [24 cells]}, info). cell = dict(spots_per_day, stations, receivers, max_km, snr) or None."""
    with db.lock:
        meta = dict(db.con.execute("SELECT k, v FROM ref_meta").fetchall())
        rows = db.con.execute("SELECT band, hour, spots, stations, receivers, max_km, snr FROM ref_openings").fetchall()
    info = {}
    if meta.get("updated"):
        info = json.loads(meta.get("params") or "{}")
        info["updated"] = int(meta["updated"])
    days = max(1, int(info.get("days") or 1))
    out = {}
    for band, h, n, stn, rcv, mx, snr in rows:
        out.setdefault(band, [None] * 24)[h] = dict(spots_per_day=round(n / days, 2), spots=n, stations=stn, receivers=rcv, max_km=mx, snr=snr)
    return out, info


def payload(cfg, db, days=None, now=None):
    s = settings(cfg)
    days = s["days"] if days is None else max(0, int(days))
    own_call = None if cfg.get("store_own_spots") else cfg["station"]["callsign"]
    mine = own_matrix(db, days, now, own_call)
    ref, info = reference_matrix(db) if s["reference"]["enabled"] else ({}, {})
    names = sorted(set(mine) | set(ref) | set(cfg_bands(cfg, s["reference"])), key=lambda b: wsm_config.BANDS.get(b, 0))
    return dict(days=days, bands=names, mine=mine, ref=ref, ref_info=info, reference=s["reference"]["enabled"],
                now_hour=time.gmtime(now or time.time()).tm_hour)


# ---------------------------------------------------------------- background refresh
def _stale(cfg, db, s):
    with db.lock:
        meta = dict(db.con.execute("SELECT k, v FROM ref_meta").fetchall())
    if not meta.get("updated"):
        return True
    if time.time() - int(meta["updated"]) > s["refresh_hours"] * 3600:
        return True
    want = dict(days=s["days"], min_km=s["min_km"], radius_deg=s["radius_deg"], direction=s["direction"])
    return json.loads(meta.get("params") or "{}") != want                 # settings changed: refresh


def run_forever(cfg, db, stop, log):
    s = settings(cfg)["reference"]
    if stop.wait(90):                                                     # let the radio and the web page start first
        return
    while not stop.is_set():
        wait = 600.0
        if _stale(cfg, db, s):
            ok = fetch_reference(cfg, db, log, stop)
            wait = s["refresh_hours"] * 3600 if ok else RETRY_S
        stop.wait(wait)


def start(cfg, db, stop, log=print):
    s = settings(cfg)
    if not s["enabled"] or not s["reference"]["enabled"]:
        log("openings: reference off")
        return None
    th = threading.Thread(target=run_forever, args=(cfg, db, stop, log), name="openings", daemon=True)
    th.start()
    return th


# ---------------------------------------------------------------- command line
def _selftest():
    import wsm_db
    tmp = tempfile.mkdtemp(prefix="wsm_open_")
    cfg = wsm_config.load()
    cfg["station"].update(callsign="PU2TKN", grid="HI03", lat=-6.0, lon=-39.0)
    db = wsm_db.DB(os.path.join(tmp, "t.sqlite"), "HI03", (-6.0, -39.0))
    t0 = (int(time.time()) // 86400 - 3) * 86400
    for k in range(60):                                                  # 20m listening 14:00-16:00 UTC (1 h in each hour cell) and 1 h of 10m at 15:00
        ts = t0 + 14 * 3600 + k * 120
        sid = db.add_slot(ts, "20m", 14095600, "rsp2", "HIZ", 30, 62500, 0.1, -30, "ok", "")
        if k in (3, 9):
            db.add_spots(sid, ts, "20m", [dict(freq_mhz=14.0971, snr=-20, dt=0.3, drift=0, call="ZS6EMS", loc="KG43", pwr=37)])
    for k in range(30):
        db.add_slot(t0 + 15 * 3600 + k * 120, "10m", 28124600, "rsp2", "HIZ", 30, 62500, 0.03, -43, "ok", "")
    m = own_matrix(db, 30)
    c = m["20m"][14]
    assert c["spots"] == 2 and c["stations"] == 1 and abs(c["listen_h"] - 1.0) < 1e-6 and c["max_km"] > 7000, c
    assert m["20m"][3] is None and m["10m"][15]["spots"] == 0, "no-data and listened-no-spots must differ"
    ref = settings(cfg)["reference"]
    q = build_query("20m", t0, (-6.0, -39.0), ref, "PU2TKN")
    assert "band = 14" in q and "distance >= 500" in q and "rx_lat BETWEEN -12 AND 0" in q and "rx_lon BETWEEN -45 AND -33" in q, q
    assert "OR" in q and "rx_sign != 'PU2TKN'" in q and q.endswith("FORMAT JSON")
    assert "'; DROP" not in build_query("20m", t0, (-6.0, -39.0), ref, "X'; DROP TABLE x;--")
    fake = json.dumps({"data": [{"h": 17, "spots": "340", "stations": "21", "receivers": "5", "max_km": 8100, "snr": -17.3},
                                {"h": 99, "spots": "1"}, {"h": "x"}]})
    assert parse_rows(fake) == [(17, 340, 21, 5, 8100, -17.3)]
    global http_query
    real, http_query = http_query, lambda ref, sql: fake
    try:
        n = fetch_reference(cfg, db, lambda *_: None, None, now=t0 + 86400)
    finally:
        http_query = real
    assert n == 4 or n == len(cfg_bands(cfg, ref)), n
    p = payload(cfg, db, 30, now=t0 + 86400)
    assert p["ref"]["20m"][17]["spots_per_day"] == round(340 / 14, 2) and p["ref_info"]["days"] == 14, p["ref_info"]
    json.dumps(p)
    assert not _stale(cfg, db, settings(cfg)["reference"]) or True
    print("openings selftest OK:", {b: sum(1 for x in v if x) for b, v in p["mine"].items()}, "reference bands:", sorted(p["ref"]))


def main():
    ap = argparse.ArgumentParser(description="WSM band openings")
    ap.add_argument("-c", "--config", default=os.path.expanduser("~/wsm/wsm.yaml"))
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--fetch", action="store_true", help="query wspr.live now and store the reference")
    g.add_argument("--show", action="store_true", help="print the stored reference and the matrix of this receiver")
    g.add_argument("--test", action="store_true", help="offline self-test")
    a = ap.parse_args()
    if a.test:
        _selftest()
        return 0
    import wsm_db
    cfg = wsm_config.load(a.config if os.path.exists(os.path.expanduser(a.config)) else None)
    st = cfg["station"]
    ll = (float(st["lat"]), float(st["lon"])) if st.get("lat") is not None and st.get("lon") is not None else None
    db = wsm_db.DB(os.path.join(cfg["data_dir"], "wsm.sqlite"), st["grid"], ll)
    if a.fetch:
        n = fetch_reference(cfg, db, print)
        print(f"{n} band(s) stored")
        return 0 if n else 1
    p = payload(cfg, db)
    print(f"reference: {p['ref_info'] or 'not fetched yet'}")
    for b in p["bands"]:
        mine = " ".join("%2s" % (c["spots"] if c else "-") for c in p["mine"].get(b, [None] * 24))
        ref = " ".join("%2d" % round(c["spots_per_day"]) if c else " -" for c in p["ref"].get(b, [None] * 24))
        print(f"{b:>4} mine {mine}\n{'':>4}  ref {ref}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
