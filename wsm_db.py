"""wsm_db.py - SQLite storage of the WSM: one row per 2-minute slot that was listened to and one row per decoded spot."""
import re
import sqlite3
import threading
import time

import wsm_geo

SCHEMA = """
CREATE TABLE IF NOT EXISTS slots (
    id        INTEGER PRIMARY KEY,
    ts        INTEGER NOT NULL,            -- start of the 2-minute slot (UTC, unix seconds)
    band      TEXT NOT NULL,
    dial_hz   INTEGER NOT NULL,
    radio     TEXT,
    antenna   TEXT,
    gain_db   REAL,
    n_spots   INTEGER NOT NULL DEFAULT 0,
    rate_hz   REAL,                        -- real sample rate of the radio stream
    peak      REAL,                        -- largest |I|,|Q| of the stream (1.0 = full scale): near 1 means overload
    rms_db    REAL,                        -- mean power of the stream, dB re full scale
    status    TEXT NOT NULL DEFAULT 'ok',  -- ok | short | error
    note      TEXT,
    UNIQUE (ts, band)
);
CREATE TABLE IF NOT EXISTS spots (
    id        INTEGER PRIMARY KEY,
    slot_id   INTEGER REFERENCES slots(id),
    ts        INTEGER NOT NULL,            -- start of the slot (UTC)
    band      TEXT NOT NULL,
    freq_hz   REAL NOT NULL,               -- RF frequency of the signal (dial + audio offset)
    snr       INTEGER,                     -- dB in 2500 Hz
    dt        REAL,                        -- s late (+) or early (-) against the nominal start 1 s after the even minute
    drift     INTEGER,                     -- Hz/min as reported by the decoder
    call      TEXT NOT NULL,
    grid      TEXT,
    power_dbm INTEGER,
    lat       REAL,                        -- centre of the locator square
    lon       REAL,
    dist_km   REAL,
    bearing   REAL,
    UNIQUE (ts, band, call, grid, power_dbm)
);
CREATE INDEX IF NOT EXISTS spots_ts ON spots(ts);
CREATE INDEX IF NOT EXISTS spots_band ON spots(band, ts);
CREATE INDEX IF NOT EXISTS spots_call ON spots(call);
CREATE INDEX IF NOT EXISTS slots_ts ON slots(ts);
CREATE TABLE IF NOT EXISTS syshist (       -- computer health, one row per 5 minutes (wsm_sys.py)
    ts        INTEGER PRIMARY KEY,         -- start of the 5 minutes (UTC)
    temp_c    REAL,                        -- mean CPU temperature
    temp_max  REAL,
    clock_mhz REAL,                        -- mean CPU clock
    clock_min REAL,                        -- lowest CPU clock seen (600 = held down)
    uv_pct    REAL,                        -- % of the samples with under-voltage
    low_clock_pct REAL,                    -- % of the samples below 1000 MHz
    samples   INTEGER
);
CREATE TABLE IF NOT EXISTS ref_openings (  -- public WSPRnet reference by band and hour of day (wsm_openings.py)
    band      TEXT NOT NULL,
    hour      INTEGER NOT NULL,            -- hour of day, UTC
    spots     INTEGER,
    stations  INTEGER,
    receivers INTEGER,
    max_km    INTEGER,
    snr       REAL,
    PRIMARY KEY (band, hour)
);
CREATE TABLE IF NOT EXISTS ref_meta (k TEXT PRIMARY KEY, v TEXT);
"""


def slot_coverage(status, note):
    """% of the samples of a slot that arrived: from the note "slot covered NN %" (written only below 90 %), else 100"""
    m = re.search(r"covered (\d+)", note or "")
    if m:
        return float(m.group(1))
    if status == "error" and "no samples" in (note or ""):
        return 0.0
    return 100.0


class DB:
    def __init__(self, path, my_grid, my_latlon=None):  # PATCH1
        self.path = path
        self.lock = threading.Lock()
        self.con = sqlite3.connect(path, check_same_thread=False, timeout=30)
        self.con.row_factory = sqlite3.Row
        self.con.execute("PRAGMA journal_mode=WAL")
        self.con.executescript(SCHEMA)
        self.my = tuple(my_latlon) if my_latlon else wsm_geo.grid_to_latlon(my_grid)

    def add_slot(self, ts, band, dial_hz, radio, antenna, gain_db, rate_hz, peak, rms_db, status="ok", note=""):
        with self.lock, self.con:
            self.con.execute(
                "INSERT OR REPLACE INTO slots (ts,band,dial_hz,radio,antenna,gain_db,n_spots,rate_hz,peak,rms_db,status,note) "
                "VALUES (?,?,?,?,?,?,0,?,?,?,?,?)",
                (int(ts), band, int(dial_hz), radio, antenna, gain_db, rate_hz, peak, rms_db, status, note))
            return self.con.execute("SELECT id FROM slots WHERE ts=? AND band=?", (int(ts), band)).fetchone()[0]

    def add_spots(self, slot_id, ts, band, spots):
        """spots: list of dicts from wsm_decode (freq_mhz, snr, dt, drift, call, loc, pwr). Returns the number stored."""
        n = 0
        with self.lock, self.con:
            for s in spots:
                ll = wsm_geo.grid_to_latlon(s.get("loc"))
                dist = brg = None
                if ll and self.my:
                    dist, brg = wsm_geo.distance_bearing(self.my[0], self.my[1], ll[0], ll[1])
                try:
                    pw = int(s.get("pwr"))
                except (TypeError, ValueError):
                    pw = None
                cur = self.con.execute(
                    "INSERT OR IGNORE INTO spots (slot_id,ts,band,freq_hz,snr,dt,drift,call,grid,power_dbm,lat,lon,dist_km,bearing) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (slot_id, int(ts), band, float(s["freq_mhz"]) * 1e6, s.get("snr"), s.get("dt"), s.get("drift"), s["call"],
                     s.get("loc"), pw, ll[0] if ll else None, ll[1] if ll else None, dist, brg))
                n += cur.rowcount
            self.con.execute("UPDATE slots SET n_spots=(SELECT COUNT(*) FROM spots WHERE slot_id=?) WHERE id=?", (slot_id, slot_id))
        return n

    def spots(self, since=0, band=None, call=None, limit=500, exclude_call=None):
        q = "SELECT * FROM spots WHERE ts>=?"
        a = [int(since)]
        if exclude_call:                                   # "hide my TX" on the page
            q += " AND UPPER(call)<>?"
            a.append(exclude_call.upper())
        if band:
            q += " AND band=?"
            a.append(band)
        if call:
            q += " AND call LIKE ?"
            a.append(call.upper().replace("*", "%"))
        q += " ORDER BY ts DESC, snr DESC LIMIT ?"
        a.append(int(limit))
        with self.lock:
            rows = [dict(r) for r in self.con.execute(q, a)]
        for r in rows:
            if r["lat"] is not None:
                r["map"] = wsm_geo.maps_url(r["lat"], r["lon"])
        return rows

    def summary(self, since, own_call=None, hide_own=False):
        """own_call: the station's own callsign, left out of the Best DX records (hearing your own TX is not DX);
        hide_own: also leave it out of the spot / station counts and of the band table"""
        own = (own_call or "").upper()
        x = " AND UPPER(call)<>?" if (hide_own and own) else ""      # extra filter on the spots table
        xa = (own,) if x else ()
        with self.lock:
            c = self.con
            tot = c.execute("SELECT COUNT(*) n, COUNT(DISTINCT call) calls, MAX(dist_km) dx FROM spots WHERE ts>=?" + x,
                            (int(since),) + xa).fetchone()
            best = c.execute("SELECT call, grid, band, dist_km FROM spots WHERE ts>=? AND dist_km IS NOT NULL AND UPPER(call)<>? "
                             "ORDER BY dist_km DESC LIMIT 1", (int(since), own)).fetchone()
            bands = [dict(r) for r in c.execute(
                "SELECT s.band band, COUNT(DISTINCT s.id) slots, "
                "(SELECT COUNT(*) FROM spots p WHERE p.band=s.band AND p.ts>=?" + x + ") spots, "
                "(SELECT COUNT(DISTINCT call) FROM spots p WHERE p.band=s.band AND p.ts>=?" + x + ") calls, "
                "MAX(s.ts) last_ts FROM slots s WHERE s.ts>=? GROUP BY s.band ORDER BY s.band",
                (int(since),) + xa + (int(since),) + xa + (int(since),))]
            last = c.execute("SELECT * FROM slots ORDER BY ts DESC LIMIT 1").fetchone()
            # record of the station: the farthest spot ever stored (independent of the period shown on the page)
            best_all = c.execute("SELECT call, grid, band, dist_km, snr, ts FROM spots WHERE dist_km IS NOT NULL AND UPPER(call)<>? "
                                 "ORDER BY dist_km DESC, ts ASC LIMIT 1", (own,)).fetchone()
        return dict(spots=tot["n"], calls=tot["calls"], dx_km=tot["dx"], best=dict(best) if best else None, bands=bands,
                    best_all=dict(best_all) if best_all else None,
                    last_slot=dict(last) if last else None, now=int(time.time()))

    def recent_slots(self, limit=12):
        with self.lock:
            return [dict(r) for r in self.con.execute("SELECT * FROM slots ORDER BY ts DESC LIMIT ?", (int(limit),))]

    def add_sys(self, row):
        with self.lock, self.con:
            self.con.execute("INSERT OR REPLACE INTO syshist (ts,temp_c,temp_max,clock_mhz,clock_min,uv_pct,low_clock_pct,samples) "
                             "VALUES (:ts,:temp_c,:temp_max,:clock_mhz,:clock_min,:uv_pct,:low_clock_pct,:samples)", row)

    def history(self, since, bucket=300):
        """health over time for the graphs: computer (syshist) and slots (coverage, input level per band).
        bucket: seconds per point; 300 keeps every 5-minute row and every slot."""
        b = max(300, int(bucket))
        with self.lock:
            sysrows = [dict(r) for r in self.con.execute(
                "SELECT (ts/?)*? ts, ROUND(AVG(temp_c),1) temp_c, MAX(temp_max) temp_max, ROUND(AVG(clock_mhz)) clock_mhz, "
                "MIN(clock_min) clock_min, MAX(uv_pct) uv_pct, ROUND(AVG(low_clock_pct),1) low_clock_pct "
                "FROM syshist WHERE ts>=? GROUP BY ts/? ORDER BY 1", (b, b, int(since), b))]
            slots = self.con.execute("SELECT ts, band, peak, rms_db, status, note, n_spots FROM slots WHERE ts>=? ORDER BY ts",
                                     (int(since),)).fetchall()
        out = []
        if b <= 300:
            for r in slots:
                out.append(dict(ts=r["ts"], band=r["band"], peak=r["peak"], rms_db=r["rms_db"],
                                cov=slot_coverage(r["status"], r["note"]), spots=r["n_spots"]))
        else:                                                  # long periods: one point per bucket and band
            agg = {}
            for r in slots:
                k = (r["ts"] // b * b, r["band"])
                a = agg.setdefault(k, dict(ts=k[0], band=k[1], peak=0.0, rms=[], cov=[], spots=0))
                a["peak"] = max(a["peak"], r["peak"] or 0.0)
                if r["rms_db"] is not None:
                    a["rms"].append(r["rms_db"])
                a["cov"].append(slot_coverage(r["status"], r["note"]))
                a["spots"] += r["n_spots"] or 0
            for a in sorted(agg.values(), key=lambda x: (x["ts"], x["band"])):
                out.append(dict(ts=a["ts"], band=a["band"], peak=a["peak"],
                                rms_db=round(sum(a["rms"]) / len(a["rms"]), 1) if a["rms"] else None,
                                cov=round(sum(a["cov"]) / len(a["cov"]), 1), spots=a["spots"]))
        return dict(sys=sysrows, slots=out, bucket=b)

    def prune(self, keep_days):
        if keep_days and keep_days > 0:
            cut = int(time.time() - keep_days * 86400)
            with self.lock, self.con:
                self.con.execute("DELETE FROM spots WHERE ts<?", (cut,))
                self.con.execute("DELETE FROM slots WHERE ts<?", (cut,))
                self.con.execute("DELETE FROM syshist WHERE ts<?", (cut,))
