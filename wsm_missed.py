#!/usr/bin/env python3
"""wsm_missed.py - signals the other receivers near this station decoded and this receiver did not.

Propagation cannot explain a miss when a receiver a few hundred km away decoded the same transmitter in the same 2-minute slot, so this
is an objective measure of the station (antenna, noise, receiver). For each band the public WSPRnet database (https://wspr.live) is
asked what the OTHER receivers in the region around this station (default 6 degrees, about 650 km) decoded in the last days, for paths
of at least `min_km`. The rows are kept in the table nb_heard and compared with this receiver's own spots, counting only the slots in
which this receiver was really listening to that band.

Reading the result: "heard by neighbours" = transmissions (one per slot and transmitter) decoded by at least one other receiver
nearby; "heard by me" = how many of those I also decoded; "strongest miss" = best SNR a neighbour had for a transmission I missed
(a miss at +5 dB is a real problem; a miss at -28 dB is at the limit of any receiver). If nobody else is in the region the table is empty.

One read-only request per band every `cycle_min` minutes, spaced; only the band, the time range and a region rounded to whole degrees are
sent (same as the openings reference).

wsm.yaml (all optional):
  missed:
    enabled: yes            # also needs openings.reference.enabled (it shares the wspr.live settings)
    days: 2                 # how much history to keep and compare
    min_km: 500             # only paths at least this long (skips local ground-wave)
    radius_deg: 6.0         # region around the station, degrees
    cycle_min: 60           # look again every this many minutes
    max_rows: 20000         # safety limit per request
Test (needs internet):  python3 wsm_missed.py -c ~/wsm/wsm.yaml --run      fetch now
Summary:                python3 wsm_missed.py -c ~/wsm/wsm.yaml --show
Offline check:          python3 wsm_missed.py --test
"""
import argparse
import json
import math
import os
import sys
import tempfile
import threading
import time

import wsm_config
import wsm_openings as op
import wsm_peers

DEFAULTS = dict(enabled=True, days=2, min_km=500, radius_deg=6.0, cycle_min=60, max_rows=20000)


def settings(cfg):
    s = dict(DEFAULTS)
    s.update(cfg.get("missed") or {})
    s["enabled"] = op._yes(s["enabled"])
    s["days"] = max(1, min(7, int(s["days"])))
    s["min_km"] = max(0, min(20000, int(s["min_km"])))
    s["radius_deg"] = max(1.0, min(30.0, float(s["radius_deg"])))
    s["cycle_min"] = max(15, int(s["cycle_min"]))
    s["max_rows"] = max(1000, min(100000, int(s["max_rows"])))
    return s


def build_query(band, since_ts, ll, s, own_call=""):
    r = s["radius_deg"]
    la0, la1 = math.floor(ll[0] - r), math.ceil(ll[0] + r)
    lo0, lo1 = math.floor(ll[1] - r), math.ceil(ll[1] + r)
    own = op._call_ok(own_call)
    skip = f" AND rx_sign != '{own}'" if own else ""
    t = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(since_ts))
    return (f"SELECT toUnixTimestamp(time) AS t, tx_sign, rx_sign, snr, distance FROM wspr.rx "
            f"WHERE band = {op.BAND_CODE[band]} AND time >= '{t}' AND distance >= {int(s['min_km'])}{skip} "
            f"AND rx_lat BETWEEN {la0} AND {la1} AND rx_lon BETWEEN {lo0} AND {lo1} "
            f"ORDER BY time DESC LIMIT {int(s['max_rows'])} FORMAT JSON")


def parse_rows(text):
    """wspr.live JSON -> [(ts, tx, rx, snr, km)]"""
    out = []
    for r in json.loads(text).get("data", []):
        t = op._num(r.get("t"), int)
        tx, rx = str(r.get("tx_sign") or "").strip(), str(r.get("rx_sign") or "").strip()
        if t is None or not tx or not rx:
            continue
        out.append((t, tx, rx, op._num(r.get("snr"), int), op._num(r.get("distance"), int)))
    return out


def fetch(cfg, db, log=print, stop=None, now=None):
    """query every band and store what the neighbours heard. Returns the number of bands stored. Never raises."""
    s = settings(cfg)
    ref = op.settings(cfg)["reference"]
    ll = op.station_latlon(cfg)
    if not ll:
        return 0
    now = int(now or time.time())
    since = now - s["days"] * 86400
    done = 0
    for i, band in enumerate(op.cfg_bands(cfg, ref)):
        if i:
            if stop is not None:
                if stop.wait(ref["pause_s"]):
                    break
            else:
                time.sleep(ref["pause_s"])
        try:
            rows = parse_rows(op.http_query(ref, build_query(band, since, ll, s, cfg["station"]["callsign"])))
        except Exception as e:
            log(f"missed: {band} failed: {type(e).__name__}: {str(e)[:100]}")
            continue
        with db.lock, db.con:
            db.con.execute("DELETE FROM nb_heard WHERE band = ? AND ts >= ?", (band, since))
            db.con.executemany("INSERT OR REPLACE INTO nb_heard (ts, band, tx, rx, snr, km) VALUES (?,?,?,?,?,?)",
                               [(t, band, tx, rx, snr, km) for t, tx, rx, snr, km in rows])
        log(f"missed: {band}: {len(rows)} reports by neighbouring receivers")
        done += 1
    if done:
        with db.lock, db.con:
            db.con.execute("INSERT OR REPLACE INTO ref_meta (k, v) VALUES ('missed_updated', ?)", (str(now),))
    return done


def compare(db, since, until=None, limit=40):
    """-> dict(updated, bands=[...], misses=[...]) for the slots from `since` on in which this receiver was listening.
    bands: band, slots (listened), neighbours (distinct receivers), heard_nb (transmissions), heard_me, pct, worst_snr_missed.
    misses: the missed transmissions, strongest first: ts, band, tx, snr (best of the neighbours), n_rx, km."""
    until = int(until or time.time() + 3600)
    since = int(since)
    with db.lock:
        listened = {(ts, band) for ts, band in db.con.execute(
            "SELECT ts, band FROM slots WHERE ts >= ? AND ts <= ? AND status IN ('ok','partial')", (since, until))}
        mine = {(ts, band, wsm_peers.base_call(c)) for ts, band, c in db.con.execute(
            "SELECT ts, band, call FROM spots WHERE ts >= ? AND ts <= ?", (since, until))}
        rows = db.con.execute("SELECT ts, band, tx, rx, snr, km FROM nb_heard WHERE ts >= ? AND ts <= ?", (since, until)).fetchall()
        upd = db.con.execute("SELECT v FROM ref_meta WHERE k='missed_updated'").fetchone()
    tx_slot = {}                                                     # (ts, band, base tx) -> [tx, best snr, receivers, km]
    for ts, band, tx, rx, snr, km in rows:
        if (ts, band) not in listened:
            continue
        e = tx_slot.setdefault((ts, band, wsm_peers.base_call(tx)), [tx, None, set(), km])
        e[2].add(wsm_peers.base_call(rx))
        if snr is not None and (e[1] is None or snr > e[1]):
            e[1] = snr
    per = {}
    for band in {b for _, b in listened}:
        per[band] = dict(band=band, slots=sum(1 for _, b in listened if b == band), nb=set(), heard_nb=0, heard_me=0, worst=None)
    misses = []
    for (ts, band, btx), (tx, snr, rxs, km) in tx_slot.items():
        p = per[band]
        p["nb"] |= rxs
        p["heard_nb"] += 1
        if (ts, band, btx) in mine:
            p["heard_me"] += 1
        else:
            misses.append(dict(ts=ts, band=band, tx=tx, snr=snr, n_rx=len(rxs), km=km))
            if snr is not None and (p["worst"] is None or snr > p["worst"]):
                p["worst"] = snr
    bands = []
    for band, p in sorted(per.items(), key=lambda kv: wsm_config.BANDS.get(kv[0], 0)):
        bands.append(dict(band=band, slots=p["slots"], neighbours=len(p["nb"]), heard_nb=p["heard_nb"], heard_me=p["heard_me"],
                          pct=round(100.0 * p["heard_me"] / p["heard_nb"]) if p["heard_nb"] else None, strongest_miss=p["worst"]))
    misses.sort(key=lambda m: (-(m["snr"] if m["snr"] is not None else -99), -m["ts"]))
    return dict(updated=int(upd[0]) if upd else None, bands=bands, misses=misses[:limit])


def run_forever(cfg, db, stop, log):
    s = settings(cfg)
    if stop.wait(420):
        return
    while not stop.is_set():
        try:
            fetch(cfg, db, log, stop)
        except Exception as e:
            log(f"missed: {type(e).__name__}: {str(e)[:100]}")
        stop.wait(s["cycle_min"] * 60)


def start(cfg, db, stop, log=print):
    if not settings(cfg)["enabled"] or not op.settings(cfg)["reference"]["enabled"]:
        log("missed: off")
        return None
    th = threading.Thread(target=run_forever, args=(cfg, db, stop, log), name="missed", daemon=True)
    th.start()
    return th


def _selftest():
    import wsm_db
    cfg = wsm_config.load()
    cfg["station"].update(callsign="PU2TKN", grid="HI03", lat=-6.0, lon=-39.0)
    s = settings(cfg)
    q = build_query("30m", 1763667720, (-6.0, -39.0), s, "PU2TKN")
    assert "band = 10" in q and "rx_sign != 'PU2TKN'" in q and "rx_lat BETWEEN -12 AND 0" in q and "FORMAT JSON" in q, q
    assert "';" not in build_query("30m", 1, (0, 0), s, "X'; DROP TABLE x;--")
    db = wsm_db.DB(os.path.join(tempfile.mkdtemp(prefix="wsm_missed_"), "t.sqlite"), "HI03", (-6.0, -39.0))
    t0 = (int(time.time()) // 86400 - 1) * 86400 + 5 * 3600
    for k in range(4):                                               # 4 listened slots on 30 m, 1 on 20 m
        db.add_slot(t0 + k * 120, "30m", 10138700, "rsp2", "HIZ", 30, 62500, 0.1, -30, "ok", "", -44)
    db.add_slot(t0, "20m", 14095600, "rsp2", "HIZ", 30, 62500, 0.1, -30, "ok", "", -44)
    sid = db.con.execute("SELECT id FROM slots WHERE ts=? AND band='30m'", (t0,)).fetchone()[0]
    db.add_spots(sid, t0, "30m", [dict(freq_mhz=10.1387, snr=-20, dt=0, drift=0, call="K9AN", loc="EN50", pwr=30)])
    fake = {"data": [
        dict(t=str(t0), tx_sign="K9AN", rx_sign="PY2AA", snr="-18", distance="7000"),            # I heard it too
        dict(t=str(t0 + 120), tx_sign="W3XYZ", rx_sign="PY2AA", snr="-12", distance="6500"),     # missed, strong
        dict(t=str(t0 + 120), tx_sign="W3XYZ", rx_sign="PY2BB-1", snr="-15", distance="6500"),
        dict(t=str(t0 + 240), tx_sign="G4ABC", rx_sign="PY2AA", snr="-27", distance="8200"),     # missed, weak
        dict(t=str(t0 + 9999), tx_sign="N1ABC", rx_sign="PY2AA", snr="-10", distance="7000"),    # slot I did not listen to: ignored
        dict(t=str(t0), tx_sign="K1ZZ", rx_sign="PY2AA", snr="-5", distance="7000", ),           # 30 m heard by a neighbour on 20 m? (stored per band query)
    ]}
    real, op.http_query = op.http_query, lambda ref, sql: json.dumps(fake)
    cfg["openings"] = {"reference": {"bands": ["30m"], "pause_s": 3.0}}
    try:
        assert fetch(cfg, db, lambda *_: None, None, now=t0 + 3600) == 1
    finally:
        op.http_query = real
    r = compare(db, t0 - 60)
    b = {x["band"]: x for x in r["bands"]}
    assert b["30m"]["slots"] == 4 and b["20m"]["slots"] == 1 and b["20m"]["heard_nb"] == 0, b
    assert b["30m"]["heard_nb"] == 4 and b["30m"]["heard_me"] == 1 and b["30m"]["pct"] == 25, b["30m"]   # K9AN, W3XYZ, G4ABC, K1ZZ
    assert b["30m"]["neighbours"] == 2 and b["30m"]["strongest_miss"] == -5, b["30m"]
    assert [m["tx"] for m in r["misses"]][:2] == ["K1ZZ", "W3XYZ"] and r["misses"][1]["n_rx"] == 2, r["misses"]
    assert r["updated"] == t0 + 3600
    print("missed selftest OK:", b["30m"])


def main():
    ap = argparse.ArgumentParser(description="WSM: what neighbouring receivers heard and I did not (wspr.live)")
    ap.add_argument("-c", "--config", default=os.path.expanduser("~/wsm/wsm.yaml"))
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--run", action="store_true", help="fetch from wspr.live now")
    g.add_argument("--show", action="store_true", help="print the comparison of the last 24 h")
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
    if a.run:
        print(fetch(cfg, db, print), "band(s) fetched")
        return 0
    r = compare(db, time.time() - 86400)
    print("%-5s %6s %5s %9s %8s %7s %s" % ("band", "slots", "recv", "heard_nb", "heard_me", "pct", "strongest_miss_snr"))
    for b in r["bands"]:
        print("%-5s %6d %5d %9d %8d %7s %s" % (b["band"], b["slots"], b["neighbours"], b["heard_nb"], b["heard_me"],
                                                "-" if b["pct"] is None else f"{b['pct']}%", b["strongest_miss"]))
    for m in r["misses"][:15]:
        print(time.strftime("%m-%d %H:%M", time.gmtime(m["ts"])), m["band"], m["tx"], "best neighbour SNR", m["snr"], "receivers", m["n_rx"], "km", m["km"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
