#!/usr/bin/env python3
"""Compare WSM reception by antenna setting (and gain) from the slots/spots tables.
usage: wsm_antenna_compare.py [DB] [--since "YYYY-MM-DD HH:MM" (UTC)] [--keep-timing]   (DB defaults to ~/wsm/data/wsm.sqlite)
Per band and antenna: slots listened, spots, spots/h, median SNR, best DX, median input
peak and mean power. Slots with an "arrival-time rate" warning (the computer was too slow or hot and the
sample timing was irregular) are skipped unless --keep-timing is given. If wsm_peers.py has run, a second table shows the SNR against
other receivers of the same transmitters (propagation cancels out). Compare the same band at similar times of day (propagation changes)."""
import sqlite3, sys, os, statistics as st, datetime as dt

import argparse
ap = argparse.ArgumentParser(description="Compare WSM reception by antenna setting.")
ap.add_argument("db", nargs="?", default="~/wsm/data/wsm.sqlite", help="path of wsm.sqlite")
ap.add_argument("--since", help='only slots from this time on, UTC, "YYYY-MM-DD HH:MM"')
ap.add_argument("--keep-timing", action="store_true", help='also count slots with an "arrival-time rate" warning')
opt = ap.parse_args()
db_path = os.path.expanduser(opt.db)
since = 0
if opt.since:
    since = int(dt.datetime.strptime(opt.since, "%Y-%m-%d %H:%M").replace(tzinfo=dt.timezone.utc).timestamp())
db = sqlite3.connect("file:%s?mode=ro" % db_path, uri=True)

rows = db.execute("""SELECT s.id, s.ts, s.band, COALESCE(s.antenna,'?'), s.gain_db, s.peak, s.rms_db, s.status, COALESCE(s.note,'')
                     FROM slots s WHERE s.ts >= ? ORDER BY s.ts""", (since,)).fetchall()
spots = {}
for slot_id, snr, dist in db.execute("SELECT slot_id, snr, dist_km FROM spots WHERE ts >= ?", (since,)):
    spots.setdefault(slot_id, []).append((snr, dist))

grp = {}
skipped = 0
for sid, ts, band, ant, gain, peak, rms, status, note in rows:
    if status != "ok": continue
    if not opt.keep_timing and "arrival-time rate" in note:
        skipped += 1
        continue
    g = grp.setdefault((band, ant, gain), {"n": 0, "peak": [], "rms": [], "snr": [], "dist": [], "spots": 0, "t0": ts, "t1": ts})
    g["n"] += 1; g["t0"] = min(g["t0"], ts); g["t1"] = max(g["t1"], ts)
    if peak is not None: g["peak"].append(peak)
    if rms is not None: g["rms"].append(rms)
    for snr, dist in spots.get(sid, []):
        g["spots"] += 1
        if snr is not None: g["snr"].append(snr)
        if dist is not None: g["dist"].append(dist)

def med(x, f="%.1f"): return f % st.median(x) if x else "-"
print("%-5s %-4s %5s %6s %6s %8s %7s %8s %7s %8s  %s" % (
    "band", "ant", "gain", "slots", "spots", "spots/h", "snr_med", "DX_km", "peak", "rms_dB", "period UTC"))
for (band, ant, gain), g in sorted(grp.items(), key=lambda kv: (kv[0][0], kv[1]["t0"])):
    hours = g["n"] * 2 / 60
    print("%-5s %-4s %5s %6d %6d %8.2f %7s %8s %7s %8s  %s .. %s" % (
        band, ant, "%g" % gain if gain is not None else "-", g["n"], g["spots"], g["spots"] / hours if hours else 0,
        med(g["snr"], "%.0f"), "%.0f" % max(g["dist"]) if g["dist"] else "-", med(g["peak"], "%.2f"), med(g["rms"]),
        dt.datetime.fromtimestamp(g["t0"], dt.timezone.utc).strftime("%m-%d %H:%M"),
        dt.datetime.fromtimestamp(g["t1"], dt.timezone.utc).strftime("%m-%d %H:%M")))
if skipped:
    print("(%d slot(s) with an 'arrival-time rate' warning skipped; use --keep-timing to count them)" % skipped)

# Against the other receivers of the same transmitter (filled by wsm_peers.py from wspr.live): propagation cancels out.
try:
    pr = db.execute("""SELECT p.band, COALESCE(sl.antenna,'?'), p.delta, sl.note FROM peer_cmp p JOIN spots sp ON sp.id = p.spot_id
                       LEFT JOIN slots sl ON sl.id = sp.slot_id WHERE p.delta IS NOT NULL AND p.ts >= ?""", (since,)).fetchall()
except sqlite3.Error:
    pr = []
pg = {}
for band, ant, d, note in pr:
    if not opt.keep_timing and "arrival-time rate" in (note or ""):
        continue
    pg.setdefault((band, ant), []).append(d)
if pg:
    print()
    print("Against peers (receivers at a similar distance that heard the same transmitter in the same slot): + = better than them")
    print("%-5s %-4s %6s %9s %7s %7s" % ("band", "ant", "spots", "median_dB", "p25", "p75"))
    for (band, ant), ds in sorted(pg.items()):
        ds.sort()
        print("%-5s %-4s %6d %+9.1f %+7.1f %+7.1f" % (band, ant, len(ds), st.median(ds), ds[int(.25 * len(ds))], ds[min(len(ds) - 1, int(.75 * len(ds)))]))
