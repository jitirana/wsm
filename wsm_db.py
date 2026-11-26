"""wsm_db.py - SQLite storage of the WSM: one row per 2-minute slot that was listened to and one row per decoded spot."""
import math
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
    noise_db  REAL,                        -- noise in the WSPR window (decoder input), dB re full scale in 2500 Hz
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
    samples   INTEGER,
    capped_pct REAL                        -- % of the samples with the CPU slowed by the firmware (capped, throttled or soft temperature limit)
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
CREATE TABLE IF NOT EXISTS spurs (         -- steady narrow carriers in the WSPR window of a slot (wsm_dsp.find_spurs)
    slot_id   INTEGER,
    ts        INTEGER NOT NULL,            -- start of the slot (UTC)
    band      TEXT NOT NULL,
    freq_hz   REAL NOT NULL,               -- RF frequency (dial + 1500 Hz + offset)
    off_hz    REAL,                        -- Hz from the centre of the WSPR window
    snr       REAL,                        -- dB in 2500 Hz, the WSPR SNR scale
    above     REAL,                        -- dB over the typical 0.37 Hz bin
    persist   REAL                         -- % of the slot in which it stood out
);
CREATE INDEX IF NOT EXISTS spurs_ts ON spurs(band, ts);
CREATE TABLE IF NOT EXISTS events (        -- things the operator did (LED on, fan off, cable moved): marked on the health charts
    id        INTEGER PRIMARY KEY,
    ts        INTEGER NOT NULL,            -- when it happened (UTC, unix seconds)
    text      TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS nb_heard (      -- what the other receivers near this station decoded (wsm_missed.py, from wspr.live)
    ts        INTEGER NOT NULL,            -- start of the slot (UTC)
    band      TEXT NOT NULL,
    tx        TEXT NOT NULL,
    rx        TEXT NOT NULL,
    snr       INTEGER,
    km        INTEGER,
    PRIMARY KEY (ts, band, tx, rx)
);
CREATE TABLE IF NOT EXISTS peer_cmp (      -- my SNR against the other receivers of the same transmitter (wsm_peers.py)
    spot_id   INTEGER PRIMARY KEY,
    ts        INTEGER NOT NULL,
    band      TEXT NOT NULL,
    call      TEXT NOT NULL,
    my_snr    INTEGER,
    my_km     REAL,
    n_peers   INTEGER,
    peer_med  REAL,
    delta     REAL,                        -- my_snr - peer_med; NULL when there were too few peers
    checked   INTEGER
);
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
        if "noise_db" not in [r[1] for r in self.con.execute("PRAGMA table_info(slots)")]:      # database of an older version
            with self.con:
                self.con.execute("ALTER TABLE slots ADD COLUMN noise_db REAL")
        if "capped_pct" not in [r[1] for r in self.con.execute("PRAGMA table_info(syshist)")]:
            with self.con:
                self.con.execute("ALTER TABLE syshist ADD COLUMN capped_pct REAL")
        self.my = tuple(my_latlon) if my_latlon else wsm_geo.grid_to_latlon(my_grid)

    def add_slot(self, ts, band, dial_hz, radio, antenna, gain_db, rate_hz, peak, rms_db, status="ok", note="", noise_db=None):
        with self.lock, self.con:
            self.con.execute(
                "INSERT OR REPLACE INTO slots (ts,band,dial_hz,radio,antenna,gain_db,n_spots,rate_hz,peak,rms_db,status,note,noise_db) "
                "VALUES (?,?,?,?,?,?,0,?,?,?,?,?,?)",
                (int(ts), band, int(dial_hz), radio, antenna, gain_db, rate_hz, peak, rms_db, status, note, noise_db))
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
        row = dict(dict(capped_pct=None), **row)
        with self.lock, self.con:
            self.con.execute("INSERT OR REPLACE INTO syshist (ts,temp_c,temp_max,clock_mhz,clock_min,uv_pct,low_clock_pct,samples,capped_pct) "
                             "VALUES (:ts,:temp_c,:temp_max,:clock_mhz,:clock_min,:uv_pct,:low_clock_pct,:samples,:capped_pct)", row)

    def add_spurs(self, slot_id, ts, band, dial_hz, spurs):
        with self.lock, self.con:
            self.con.executemany("INSERT INTO spurs (slot_id, ts, band, freq_hz, off_hz, snr, above, persist) VALUES (?,?,?,?,?,?,?,?)",
                                 [(slot_id, int(ts), band, float(dial_hz) + 1500.0 + x["off_hz"], x["off_hz"], x["snr"], x["above"],
                                   x["persist"]) for x in spurs])

    def spur_summary(self, since, tol_hz=1.0, limit=12):
        """steady carriers grouped by band and frequency (within tol_hz): in how many of the slots of the band each one was
        seen, its SNR (median and strongest) and when. A carrier seen in most slots is a local source (power supply, LED
        driver, a clock harmonic); one seen once is more likely a station that sent a plain carrier."""
        with self.lock:
            rows = self.con.execute("SELECT ts, band, freq_hz, off_hz, snr FROM spurs WHERE ts>=? ORDER BY band, freq_hz", (int(since),)).fetchall()
            listened = dict(self.con.execute("SELECT band, COUNT(*) FROM slots WHERE ts>=? AND status!='error' GROUP BY band", (int(since),)).fetchall())
        groups = []
        for r in rows:
            g = groups[-1] if groups else None
            if g and g["band"] == r["band"] and r["freq_hz"] - g["f"][-1] <= tol_hz:
                g["f"].append(r["freq_hz"]); g["off"].append(r["off_hz"]); g["snr"].append(r["snr"]); g["ts"].add(r["ts"])
            else:
                groups.append(dict(band=r["band"], f=[r["freq_hz"]], off=[r["off_hz"]], snr=[r["snr"]], ts={r["ts"]}))
        out = []
        for g in groups:
            sn = sorted(g["snr"]); n = len(g["ts"]); tot = listened.get(g["band"], 0)
            out.append(dict(band=g["band"], freq_hz=round(sorted(g["f"])[len(g["f"]) // 2], 1), off_hz=round(sorted(g["off"])[len(g["off"]) // 2], 1),
                            slots=n, band_slots=tot, pct=round(100.0 * n / tot) if tot else None,
                            snr_med=sn[len(sn) // 2], snr_max=sn[-1], first=min(g["ts"]), last=max(g["ts"])))
        out.sort(key=lambda x: (-x["slots"], -x["snr_med"]))
        return out[:limit]

    def thermal_daily(self, since, hist_s=300, tz=time.localtime):
        """thermal health per local day: how long the CPU ran slowed down and whether short slots coincided with it.
        One syshist row covers `hist_s` seconds, so minutes = sum(pct) / 100 * hist_s / 60. A slot is "during a slowdown"
        when a 5-minute row it overlaps (start or end) had any capped, under-voltage or low-clock sample."""
        with self.lock:
            rows = [dict(r) for r in self.con.execute("SELECT * FROM syshist WHERE ts>=? ORDER BY ts", (int(since),))]
            slots = self.con.execute("SELECT ts, status, note FROM slots WHERE ts>=? ORDER BY ts", (int(since),)).fetchall()
        day = lambda ts: time.strftime("%Y-%m-%d", tz(ts))
        f = hist_s / 60.0 / 100.0
        out, slow = {}, set()
        for r in rows:
            d = out.setdefault(day(r["ts"]), dict(day=day(r["ts"]), minutes=0, temp_max=None, temp_sum=0.0, temp_n=0,
                                                   capped_min=None, low_clock_min=0.0, uv_min=None,
                                                   slots=0, short=0, short_slow=0, slow_slots=0))
            d["minutes"] += hist_s / 60.0
            if r["temp_max"] is not None:
                d["temp_max"] = max(d["temp_max"] or -99, r["temp_max"])
            if r["temp_c"] is not None:
                d["temp_sum"] += r["temp_c"]; d["temp_n"] += 1
            if r["capped_pct"] is not None:
                d["capped_min"] = (d["capped_min"] or 0.0) + r["capped_pct"] * f
            if r["uv_pct"] is not None:
                d["uv_min"] = (d["uv_min"] or 0.0) + r["uv_pct"] * f
            d["low_clock_min"] += (r["low_clock_pct"] or 0.0) * f
            if (r["capped_pct"] or 0) > 0 or (r["uv_pct"] or 0) > 0 or (r["low_clock_pct"] or 0) > 0:
                slow.add(r["ts"])
        for s in slots:
            d = out.get(day(s["ts"]))
            if d is None:
                continue
            during = any((t // hist_s * hist_s) in slow for t in (s["ts"], s["ts"] + 119))
            short = slot_coverage(s["status"], s["note"]) < 100
            d["slots"] += 1
            d["slow_slots"] += during
            d["short"] += short
            d["short_slow"] += short and during
        res = []
        for d in sorted(out.values(), key=lambda x: x["day"], reverse=True):
            n = d.pop("temp_n"); t = d.pop("temp_sum")
            d["temp_mean"] = round(t / n, 1) if n else None
            for k in ("minutes", "capped_min", "low_clock_min", "uv_min"):
                if d[k] is not None:
                    d[k] = round(d[k], 1)
            res.append(d)
        return res

    # ---- operator events (marked on the charts) ----
    def add_event(self, ts, text):
        text = " ".join(str(text).split())[:80]
        if not text:
            raise ValueError("empty text")
        with self.lock, self.con:
            cur = self.con.execute("INSERT INTO events (ts, text) VALUES (?, ?)", (int(ts), text))
            return cur.lastrowid

    def del_event(self, event_id):
        with self.lock, self.con:
            return self.con.execute("DELETE FROM events WHERE id=?", (int(event_id),)).rowcount

    def events(self, since, until=None):
        with self.lock:
            return [dict(r) for r in self.con.execute(
                "SELECT id, ts, text FROM events WHERE ts>=? AND ts<=? ORDER BY ts", (int(since), int(until or time.time() + 86400)))]

    def noise_baseline(self, days=14, min_slots=8, now=None):
        """typical noise of each band at each hour of the day (UTC): the median of noise_db over the last `days` days.
        -> {band: [24 values, None where there are fewer than min_slots slots or only one day of data]}.
        The median ignores a few odd days (a test with the antenna off, a thunderstorm)."""
        since = int(now or time.time()) - int(days) * 86400
        groups = {}
        with self.lock:
            for ts, band, nz in self.con.execute(
                    "SELECT ts, band, noise_db FROM slots WHERE ts>=? AND noise_db IS NOT NULL AND status IN ('ok','partial')", (since,)):
                groups.setdefault((band, ts % 86400 // 3600), []).append((ts // 86400, nz))
        out = {}
        for (band, h), vals in groups.items():
            if len(vals) < min_slots or len({d for d, _ in vals}) < 2:
                continue
            v = sorted(n for _, n in vals)
            m = len(v) // 2
            out.setdefault(band, [None] * 24)[h] = round(v[m] if len(v) % 2 else (v[m - 1] + v[m]) / 2, 2)
        return out

    # ---- before/after of a change ----
    @staticmethod
    def _mean_sd(v):
        n = len(v)
        m = sum(v) / n
        sd = math.sqrt(sum((x - m) ** 2 for x in v) / (n - 1)) if n > 1 else 0.0
        return m, sd

    def window_stats(self, t0, t1):
        """slots with t0 <= ts < t1 -> {band: dict(n, noise, noise_sd, rms)} (mean noise_db and stream power, dB)"""
        by = {}
        with self.lock:
            for band, nz, rms in self.con.execute(
                    "SELECT band, noise_db, rms_db FROM slots WHERE ts>=? AND ts<? AND noise_db IS NOT NULL AND status IN ('ok','partial')",
                    (int(t0), int(t1))):
                by.setdefault(band, []).append((nz, rms))
        out = {}
        for band, v in by.items():
            m, sd = self._mean_sd([a for a, _ in v])
            r = [b for _, b in v if b is not None]
            out[band] = dict(n=len(v), noise=m, noise_sd=sd, rms=sum(r) / len(r) if r else None)
        return out

    def event_effect(self, event_id, before_min=30, settle_min=10, after_min=30, now=None):
        """what changed in each band around an event: the mean noise in the `before_min` minutes before it against the mean from
        `settle_min` minutes after it (lamps and power supplies take minutes to settle) for `after_min` minutes. Both windows stop at the
        neighbouring events, so another change does not leak in. -> dict(event, a=[t0,t1], b=[t0,t1], bands=[...]) or None"""
        now = int(now or time.time())
        with self.lock:
            ev = self.con.execute("SELECT id, ts, text FROM events WHERE id=?", (int(event_id),)).fetchone()
            if not ev:
                return None
            prev = self.con.execute("SELECT MAX(ts) FROM events WHERE ts<?", (ev["ts"],)).fetchone()[0]
            nxt = self.con.execute("SELECT MIN(ts) FROM events WHERE ts>?", (ev["ts"],)).fetchone()[0]
        t, settle = ev["ts"], settle_min * 60
        a0, a1 = t - before_min * 60, t
        if prev is not None:
            a0 = max(a0, prev + settle)
        b0 = t + settle
        b1 = min(b0 + after_min * 60, nxt if nxt is not None else b0 + after_min * 60, now)
        sa = self.window_stats(a0, a1) if a1 > a0 else {}
        sb = self.window_stats(b0, b1) if b1 > b0 else {}
        bands = []
        for band in sorted(set(sa) & set(sb)):
            a, b = sa[band], sb[band]
            if a["n"] < 2 or b["n"] < 2:
                continue
            d = b["noise"] - a["noise"]
            se = math.sqrt(a["noise_sd"] ** 2 / a["n"] + b["noise_sd"] ** 2 / b["n"])
            bands.append(dict(band=band, n_before=a["n"], n_after=b["n"], before=round(a["noise"], 2), after=round(b["noise"], 2),
                              delta=round(d, 2), se=round(se, 2),
                              rms_delta=None if a["rms"] is None or b["rms"] is None else round(b["rms"] - a["rms"], 2),
                              clear=bool(abs(d) >= 0.5 and abs(d) > 2 * se)))
        return dict(event=dict(ev), a=[a0, a1], b=[b0, b1], bands=bands)

    def noise_steps(self, since, min_db=1.5, k=4, gap_s=1800, sep_s=1200):
        """level shifts of the noise, found without anybody saying what changed: for each band the median of k slots after a point against the
        median of the k slots before it, in runs of the same band without a gap of more than `gap_s` seconds (the rotation leaves other
        bands in between, but not for long). A shift needs at least `min_db` and 3 times the scatter of the data; of several candidates close to
        each other (`sep_s`) only the largest is kept. -> [dict(ts, band, before, after, delta)] oldest first"""
        by = {}
        with self.lock:
            for ts, band, nz in self.con.execute(
                    "SELECT ts, band, noise_db FROM slots WHERE ts>=? AND noise_db IS NOT NULL AND status IN ('ok','partial') ORDER BY ts",
                    (int(since),)):
                by.setdefault(band, []).append((ts, nz))

        def med(v):
            v = sorted(v)
            m = len(v) // 2
            return v[m] if len(v) % 2 else (v[m - 1] + v[m]) / 2

        found = []
        for band, rows in by.items():
            segs, cur = [], []
            for r in rows:
                if cur and r[0] - cur[-1][0] > gap_s:
                    segs.append(cur)
                    cur = []
                cur.append(r)
            segs.append(cur)
            cand = []
            for seg in segs:
                for i in range(k, len(seg) - k + 1):
                    bef, aft = [x for _, x in seg[i - k:i]], [x for _, x in seg[i:i + k]]
                    mb, ma = med(bef), med(aft)
                    scatter = max(0.3, 1.4826 * med([abs(x - mb) for x in bef] + [abs(x - ma) for x in aft]))
                    d = ma - mb
                    if abs(d) >= min_db and abs(d) >= 3 * scatter * math.sqrt(2.0 / k):
                        cand.append((abs(d), seg[i][0], mb, ma))
            kept = []
            for ad, ts, mb, ma in sorted(cand, reverse=True):
                if all(abs(ts - t2) > sep_s for _, t2, _, _ in kept):
                    kept.append((ad, ts, mb, ma))
            found += [dict(ts=ts, band=band, before=round(mb, 2), after=round(ma, 2), delta=round(ma - mb, 2)) for _, ts, mb, ma in kept]
        return sorted(found, key=lambda x: x["ts"])

    def history(self, since, bucket=300):
        """health over time for the graphs: computer (syshist) and slots (coverage, input level per band).
        bucket: seconds per point; 300 keeps every 5-minute row and every slot."""
        b = max(300, int(bucket))
        with self.lock:
            sysrows = [dict(r) for r in self.con.execute(
                "SELECT (ts/?)*? ts, ROUND(AVG(temp_c),1) temp_c, MAX(temp_max) temp_max, ROUND(AVG(clock_mhz)) clock_mhz, "
                "MIN(clock_min) clock_min, MAX(uv_pct) uv_pct, ROUND(AVG(low_clock_pct),1) low_clock_pct "
                "FROM syshist WHERE ts>=? GROUP BY ts/? ORDER BY 1", (b, b, int(since), b))]
            slots = self.con.execute("SELECT ts, band, peak, rms_db, noise_db, status, note, n_spots FROM slots WHERE ts>=? ORDER BY ts",
                                     (int(since),)).fetchall()
        out = []
        if b <= 300:
            for r in slots:
                out.append(dict(ts=r["ts"], band=r["band"], peak=r["peak"], rms_db=r["rms_db"], noise_db=r["noise_db"],
                                cov=slot_coverage(r["status"], r["note"]), spots=r["n_spots"]))
        else:                                                  # long periods: one point per bucket and band
            agg = {}
            for r in slots:
                k = (r["ts"] // b * b, r["band"])
                a = agg.setdefault(k, dict(ts=k[0], band=k[1], peak=0.0, rms=[], noise=[], cov=[], spots=0))
                a["peak"] = max(a["peak"], r["peak"] or 0.0)
                if r["rms_db"] is not None:
                    a["rms"].append(r["rms_db"])
                if r["noise_db"] is not None:
                    a["noise"].append(r["noise_db"])
                a["cov"].append(slot_coverage(r["status"], r["note"]))
                a["spots"] += r["n_spots"] or 0
            for a in sorted(agg.values(), key=lambda x: (x["ts"], x["band"])):
                out.append(dict(ts=a["ts"], band=a["band"], peak=a["peak"],
                                rms_db=round(sum(a["rms"]) / len(a["rms"]), 1) if a["rms"] else None,
                                noise_db=round(sum(a["noise"]) / len(a["noise"]), 1) if a["noise"] else None,
                                cov=round(sum(a["cov"]) / len(a["cov"]), 1), spots=a["spots"]))
        return dict(sys=sysrows, slots=out, bucket=b, events=self.events(since), base=self.noise_baseline())

    def prune(self, keep_days):
        if keep_days and keep_days > 0:
            cut = int(time.time() - keep_days * 86400)
            with self.lock, self.con:
                self.con.execute("DELETE FROM spots WHERE ts<?", (cut,))
                self.con.execute("DELETE FROM slots WHERE ts<?", (cut,))
                self.con.execute("DELETE FROM syshist WHERE ts<?", (cut,))
                self.con.execute("DELETE FROM spurs WHERE ts<?", (cut,))
                self.con.execute("DELETE FROM nb_heard WHERE ts<?", (cut,))
