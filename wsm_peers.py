#!/usr/bin/env python3
"""wsm_peers.py - how does this receiver compare with the others that heard the same station in the same slot?

For each spot of this receiver the public WSPRnet database (https://wspr.live) is asked who else decoded the same transmitter in the
same 2-minute slot and band. Receivers at a similar distance from that transmitter (so a similar path) are the "peers"; the result
is  delta = my SNR - median SNR of the peers  (dB). Propagation cancels out, so the number measures the station (antenna, noise,
receiver) and is a fair way to compare antennas, gains or a new location: positive = better than the peers, negative = worse.

One small read-only request per spot (no JOINs, exact slot time), spaced to stay under the 20 requests/minute limit. Only spots with
a known distance are used, and a spot is looked up once it is `delay_min` old (the database needs a few minutes to ingest it).
Nothing about this station is sent except the callsign of the heard transmitter, the band and the slot time.

wsm.yaml (all optional):
  peers:
    enabled: yes
    window_pct: 10          # peers = receivers whose distance to the transmitter is within this % of mine ...
    window_min_km: 700      # ... or this many km, whichever is larger
    min_peers: 3            # fewer peers than this: the spot is kept but has no delta
    delay_min: 30           # look a spot up when it is this old
    per_cycle: 40           # spots per round (about 3 min), one round every cycle_min minutes
    cycle_min: 20
    days: 3                 # do not look at spots older than this
Test (needs internet):  python3 wsm_peers.py -c ~/wsm/wsm.yaml --run      look up pending spots now
Summary:                python3 wsm_peers.py -c ~/wsm/wsm.yaml --show [--since "YYYY-MM-DD HH:MM"]
Offline check:          python3 wsm_peers.py --test
"""
import argparse
import json
import os
import re
import statistics
import sys
import tempfile
import threading
import time
import datetime as dt

import wsm_config
import wsm_openings as op

DEFAULTS = dict(enabled=True, window_pct=10.0, window_min_km=700.0, min_peers=3, delay_min=30, per_cycle=40, cycle_min=20, days=3)


def settings(cfg):
    s = dict(DEFAULTS)
    s.update(cfg.get("peers") or {})
    s["enabled"] = op._yes(s["enabled"])
    s["window_pct"] = max(1.0, min(50.0, float(s["window_pct"])))
    s["window_min_km"] = max(100.0, min(5000.0, float(s["window_min_km"])))
    s["min_peers"] = max(2, int(s["min_peers"]))
    s["delay_min"] = max(5, int(s["delay_min"]))
    s["per_cycle"] = max(1, min(200, int(s["per_cycle"])))
    s["cycle_min"] = max(5, int(s["cycle_min"]))
    s["days"] = max(1, min(30, int(s["days"])))
    return s


def base_call(c):
    """K9TRV-2, KM3T-5, K9TRV: one station with several receivers. -> 'K9TRV'"""
    return re.split(r"[-/]", str(c).upper())[0] or str(c).upper()


def pick_peers(rows, my_call, my_km, s):
    """rows: [(rx_sign, snr, distance_km)] of everybody who heard the transmitter. -> list of peer SNRs, one per station."""
    mine = base_call(my_call)
    win = max(s["window_min_km"], my_km * s["window_pct"] / 100.0)
    best = {}
    for rx, snr, km in rows:
        if snr is None or km is None or abs(km - my_km) > win:
            continue
        b = base_call(rx)
        if b == mine:
            continue
        if b not in best or snr > best[b]:                        # several receivers of one station: its best one
            best[b] = snr
    return sorted(best.values())


def evaluate(rows, my_call, my_snr, my_km, s):
    """-> (n_peers, median, delta or None)"""
    peers = pick_peers(rows, my_call, my_km, s)
    if len(peers) < s["min_peers"]:
        return len(peers), (statistics.median(peers) if peers else None), None
    med = statistics.median(peers)
    return len(peers), med, round(my_snr - med, 1)


def build_query(band, ts, tx_call, ref_url=None):
    t = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(ts))
    return (f"SELECT rx_sign, snr, distance FROM wspr.rx WHERE band = {op.BAND_CODE[band]} AND time = '{t}' "
            f"AND tx_sign = '{op._call_ok(tx_call)}' FORMAT JSON")


def parse_rows(text):
    out = []
    for r in json.loads(text).get("data", []):
        out.append((str(r.get("rx_sign") or ""), op._num(r.get("snr"), int), op._num(r.get("distance"), float)))
    return out


def pending(db, s, now):
    """spots old enough, not looked up yet"""
    old, new = now - s["days"] * 86400, now - s["delay_min"] * 60
    with db.lock:
        return db.con.execute(
            "SELECT sp.id, sp.ts, sp.band, sp.call, sp.snr, sp.dist_km FROM spots sp LEFT JOIN peer_cmp p ON p.spot_id = sp.id "
            "WHERE p.spot_id IS NULL AND sp.ts BETWEEN ? AND ? AND sp.snr IS NOT NULL AND sp.dist_km IS NOT NULL "
            "ORDER BY sp.ts DESC LIMIT 400", (old, new)).fetchall()


def run_once(cfg, db, log=print, stop=None, now=None, limit=None):
    """look up pending spots (newest first). Returns the number stored. Never raises."""
    s = settings(cfg)
    ref = op.settings(cfg)["reference"]
    now = int(now or time.time())
    my = cfg["station"]["callsign"]
    done = 0
    for i, (sid, ts, band, call, snr, km) in enumerate(pending(db, s, now)[: limit or s["per_cycle"]]):
        if band not in op.BAND_CODE:
            continue
        if i:
            if stop is not None:
                if stop.wait(max(3.2, ref["pause_s"] / 2)):
                    break
            else:
                time.sleep(3.2)
        try:
            rows = parse_rows(op.http_query(ref, build_query(band, ts, call)))
        except Exception as e:
            log(f"peers: lookup failed: {type(e).__name__}: {str(e)[:100]}")
            break                                                       # site down or limit hit: try again next round
        if not rows and now - ts < 6 * 3600:                            # not ingested yet: try again later
            continue
        n, med, delta = evaluate(rows, my, snr, km, s)
        with db.lock, db.con:
            db.con.execute("INSERT OR REPLACE INTO peer_cmp (spot_id, ts, band, call, my_snr, my_km, n_peers, peer_med, delta, checked) "
                           "VALUES (?,?,?,?,?,?,?,?,?,?)", (sid, ts, band, call, snr, km, n, med, delta, now))
        done += 1
    if done:
        log(f"peers: {done} spot(s) compared")
    return done


def run_forever(cfg, db, stop, log):
    s = settings(cfg)
    if stop.wait(300):
        return
    while not stop.is_set():
        try:
            run_once(cfg, db, log, stop)
        except Exception as e:
            log(f"peers: {type(e).__name__}: {str(e)[:100]}")
        stop.wait(s["cycle_min"] * 60)


def start(cfg, db, stop, log=print):
    s = settings(cfg)
    if not s["enabled"] or not op.settings(cfg)["reference"]["enabled"]:
        log("peers: off")
        return None
    th = threading.Thread(target=run_forever, args=(cfg, db, stop, log), name="peers", daemon=True)
    th.start()
    return th


def summary(db, since=0):
    """-> rows (band, antenna, n_spots, median_delta, p25, p75, median_peers) for spots that have a delta"""
    with db.lock:
        data = db.con.execute(
            "SELECT p.band, COALESCE(sl.antenna,'?'), p.delta, p.n_peers FROM peer_cmp p JOIN spots sp ON sp.id = p.spot_id "
            "LEFT JOIN slots sl ON sl.id = sp.slot_id WHERE p.delta IS NOT NULL AND p.ts >= ?", (since,)).fetchall()
    g = {}
    for band, ant, d, n in data:
        g.setdefault((band, ant), ([], []))
        g[(band, ant)][0].append(d)
        g[(band, ant)][1].append(n)
    out = []
    for (band, ant), (ds, ns) in sorted(g.items(), key=lambda kv: (wsm_config.BANDS.get(kv[0][0], 0), kv[0][1])):
        ds.sort()
        q = lambda f: ds[min(len(ds) - 1, int(f * len(ds)))]
        out.append((band, ant, len(ds), statistics.median(ds), q(0.25), q(0.75), statistics.median(ns)))
    return out


def _selftest():
    import wsm_db
    cfg = wsm_config.load()
    cfg["station"].update(callsign="PU2TKN", grid="HI03", lat=-6.0, lon=-39.0)
    s = settings(cfg)
    rows = [("K9TRV", 9, 7500), ("K9TRV-2", 3, 7500), ("PY2GN", -18, 7650), ("PP5OW", -17, 7900), ("PT2FHC", -19, 7890),
            ("FAR", 2, 13000), ("EA8BFK", -2, 7570), ("PU2TKN", -24, 7423), ("NOSNR", None, 7400)]
    peers = pick_peers(rows, "PU2TKN", 7423, s)
    assert peers == sorted([9, -18, -17, -19, -2]), peers                  # K9TRV merged (best), FAR out of the window, own out
    n, med, d = evaluate(rows, "PU2TKN", -24, 7423, s)
    assert (n, med, d) == (5, -17, -7.0), (n, med, d)
    assert evaluate(rows[:2], "PU2TKN", -24, 7423, s)[2] is None            # too few peers
    assert "band = 14" in build_query("20m", 1763667720, "ZS6EMS") and "tx_sign = 'ZS6EMS'" in build_query("20m", 1763667720, "ZS6EMS")
    assert "';" not in build_query("20m", 1, "X'; DROP TABLE x;--")
    db = wsm_db.DB(os.path.join(tempfile.mkdtemp(prefix="wsm_peers_"), "t.sqlite"), "HI03", (-6.0, -39.0))
    t0 = (int(time.time()) // 86400 - 1) * 86400 + 19 * 3600 + 42 * 60
    sid = db.add_slot(t0, "20m", 14095600, "rsp2", "A", 30, 62500, 0.1, -30, "ok", "")
    db.add_spots(sid, t0, "20m", [dict(freq_mhz=14.0971, snr=-24, dt=0.3, drift=0, call="ZS6EMS", loc="KG43", pwr=37)])
    fake = json.dumps({"data": [dict(rx_sign=r, snr=str(v) if v is not None else None, distance=str(k)) for r, v, k in rows]})
    real, op.http_query = op.http_query, lambda ref, sql: fake
    try:
        assert run_once(cfg, db, lambda *_: None, None, now=t0 + 7200, limit=5) == 1
        assert run_once(cfg, db, lambda *_: None, None, now=t0 + 7200) == 0           # already done
    finally:
        op.http_query = real
    r = summary(db)
    assert r and r[0][:3] == ("20m", "A", 1) and abs(r[0][3] - (-7.0)) < 1, r
    print("peers selftest OK:", r)


def main():
    ap = argparse.ArgumentParser(description="WSM peer comparison (wspr.live)")
    ap.add_argument("-c", "--config", default=os.path.expanduser("~/wsm/wsm.yaml"))
    ap.add_argument("--since", help='UTC "YYYY-MM-DD HH:MM" for --show')
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--run", action="store_true", help="look up pending spots now")
    g.add_argument("--show", action="store_true", help="print the summary by band and antenna")
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
        n = run_once(cfg, db, print, None, limit=60)
        print(f"{n} spot(s) compared; {len(pending(db, settings(cfg), int(time.time())))} still pending")
        return 0
    since = 0
    if a.since:
        since = int(dt.datetime.strptime(a.since, "%Y-%m-%d %H:%M").replace(tzinfo=dt.timezone.utc).timestamp())
    print("%-5s %-4s %6s %9s %7s %7s %9s" % ("band", "ant", "spots", "median_dB", "p25", "p75", "peers_med"))
    for band, ant, n, m, q1, q3, pn in summary(db, since):
        print("%-5s %-4s %6d %+9.1f %+7.1f %+7.1f %9.0f" % (band, ant, n, m, q1, q3, pn))
    print("delta = my SNR minus the median SNR of receivers at a similar distance that heard the same transmitter; + = better than them")
    return 0


if __name__ == "__main__":
    sys.exit(main())
