#!/usr/bin/env python3
"""wsm_service.py - WSPR System Monitor: band scheduler, capture, decode, SQLite, web page.

Every radio runs its own thread: it records one 2-minute WSPR slot per band of its rotation, retunes in the gap, and hands the
recording to a decode worker (DSP to 375 S/s, wsm_decode, database). The web page is read-only.
"""
import argparse
import json
import os
import queue
import signal
import subprocess
import sys
import tempfile
import threading
import time

import numpy as np

import wsm_capture
import wsm_config
import wsm_backup
import wsm_db
import wsm_health
import wsm_dsp
import wsm_report
import wsm_sys
import wsm_wsprnet
import wsm_web

SLOT_S = 120
RECORD_S = 116          # leaves 4 s to stop, retune and pre-roll before the next slot
DT_OFFSET = 1.0         # the decoder's dt is relative to second 2 of the slot; WSPR nominally starts at second 1
MIN_LEAD_S = 3.0        # do not try to catch a slot that starts sooner than this


def log(msg):
    print(time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()) + "Z " + msg, flush=True)


def band_cfg(rcfg, band):
    c = dict(rcfg)
    c.update((rcfg.get("bands") or {}).get(band) or {})
    return c


def next_slot(now):
    return (int(now) // SLOT_S + 1) * SLOT_S


def run_decoder(cfg, iq, dial_hz):
    """iq: 45000 complex64 samples -> list of spot dicts (dt already referred to the nominal start)"""
    exe = cfg["decoder"]["path"]
    flags = ["-H"] if cfg["decoder"].get("hashtable") else []
    st = cfg["station"]
    # PI: the decoder input (~360 kB per slot) goes to RAM (/dev/shm), not to the SD card: ~260 MB of writes a day saved
    tmpdir = "/dev/shm" if os.access("/dev/shm", os.W_OK) else cfg["data_dir"]
    fd, path = tempfile.mkstemp(prefix="wsm_dec_", suffix=".iq", dir=tmpdir)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(np.ascontiguousarray(iq, np.complex64).tobytes())
        keep = int(cfg.get("debug_keep_iq", 0) or 0)
        if keep > 0:
            import glob, shutil
            dd = os.path.join(cfg["data_dir"], "debug")
            os.makedirs(dd, exist_ok=True)
            shutil.copy(path, os.path.join(dd, f"iq_{int(dial_hz)}_{int(time.time())}.iq"))
            for old in sorted(glob.glob(os.path.join(dd, "iq_*.iq")))[:-keep]:
                os.unlink(old)
        cmd = [exe, "-f", str(int(dial_hz)), "-c", st["callsign"], "-l", st["grid"]] + flags + [path]
        p = subprocess.run(cmd, cwd=cfg["data_dir"], capture_output=True, text=True, timeout=110)
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass
    spots = []
    for line in p.stdout.splitlines():
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if d.get("summary"):
            continue
        if d.get("dt") is not None:
            d["dt"] = round(float(d["dt"]) + DT_OFFSET, 1)
        spots.append(d)
    if p.returncode != 0:
        raise RuntimeError(f"decoder exit {p.returncode}: {p.stderr.strip()[:200]}")
    return spots


def _dsp_and_decode(cfg, x_path, rate, t0, slot, offset_hz, dial):
    """runs in the decode PROCESS (see DecodePool): stream file -> 375 S/s grid -> wsm_decode. Returns (spots, coverage, error)."""
    x = np.load(x_path, mmap_mode="r")                # read block by block by wsm_dsp: the slot is never copied whole
    iq, cov = wsm_dsp.to_wspr_grid(x, rate, t0, slot, offset_hz)
    del x
    try:
        return run_decoder(cfg, iq, dial), cov, ""
    except Exception as e:
        return [], cov, str(e)


def _decode_proc_init():
    os.nice(10)                                        # the capture thread of the main process comes first


class DecodePool:
    """
    PI: DSP + decoder in a separate process. In a thread they held the Python GIL for ~10 s per slot on the Pi 3B, the capture
    thread could not empty the SDRplay buffers in time and the start of every slot lost samples ("O" overflows).
    The recorded stream goes through a file in /dev/shm (RAM), so nothing big is pickled through a pipe.
    """

    def __init__(self, cfg):
        import multiprocessing
        from concurrent.futures import ProcessPoolExecutor
        self.cfg = cfg
        self.shm = "/dev/shm" if os.access("/dev/shm", os.W_OK) else cfg["data_dir"]
        self._make = lambda: ProcessPoolExecutor(1, mp_context=multiprocessing.get_context("spawn"), initializer=_decode_proc_init)
        self.ex = self._make()

    def run(self, x, rate, t0, slot, offset_hz, dial):
        fd, path = tempfile.mkstemp(prefix="wsm_slot_", suffix=".npy", dir=self.shm)
        try:
            with os.fdopen(fd, "wb") as f:
                np.save(f, x)
            try:
                return self.ex.submit(_dsp_and_decode, self.cfg, path, rate, t0, slot, offset_hz, dial).result(timeout=300)
            except Exception:
                self.ex.shutdown(wait=False, cancel_futures=True)   # broken or stuck worker: start a fresh one next time
                self.ex = self._make()
                raise
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass

    def close(self):
        self.ex.shutdown(wait=True, cancel_futures=True)


def is_own_call(call, own):
    """True when a decoded callsign is the station's own: PU2TKN, <PU2TKN> (hashed), PU2TKN/P, ZS6/PU2TKN"""
    c, own = (call or "").strip().strip("<>").upper(), (own or "").strip().upper()
    return bool(own) and (c == own or own in c.split("/"))


def _yes(v):
    return v.strip().lower() in ("1", "true", "yes", "y", "on", "sim", "s") if isinstance(v, str) else bool(v)


def process(cfg, db, job, pool=None):
    """one recorded slot -> database. Returns (n_spots, note). With `pool` (DecodePool) the DSP and decoder run in another process."""
    rc, band, dial, slot, rec = job["rcfg"], job["band"], job["dial"], job["slot"], job["rec"]
    bc = band_cfg(rc, band)
    note = rec.get("note", "")
    status = "ok"
    n = 0
    if rec["x"].size == 0:
        status, note = "error", note or "no samples"
        db.add_slot(slot, band, dial, rc["name"], bc["antenna"], bc["gain_db"], rec["rate"], rec["peak"], rec["rms_db"], status, note)
        return 0, note
    if pool is not None:
        spots, cov, err = pool.run(rec["x"], rec["rate"], rec["t0"], slot, float(bc["offset_hz"]), dial)
        rec["x"] = np.zeros(1, np.complex64)          # free the slot now, not when the job is dropped
    else:
        iq, cov = wsm_dsp.to_wspr_grid(rec["x"], rec["rate"], rec["t0"], slot, float(bc["offset_hz"]))
        try:
            spots, err = run_decoder(cfg, iq, dial), ""
        except Exception as e:
            spots, err = [], str(e)
    if cov < 0.9:
        status = "partial"
        note = (note + " " if note else "") + f"slot covered {cov * 100:.0f} %"
    if rec["peak"] > 0.9:
        note = (note + " " if note else "") + "input overload: lower the gain"
    if err:
        spots, status = [], "error"
        note = (note + " " if note else "") + f"decoder: {err}"
    if spots and not _yes(cfg.get("store_own_spots", False)):         # TX = RX: hearing your own beacon is not stored
        mine = [sp for sp in spots if is_own_call(sp.get("call"), cfg["station"]["callsign"])]
        if mine:
            spots = [sp for sp in spots if sp not in mine]
            m = mine[0]                                                   # kept in the log/slot note: handy to calibrate DT
            note = (note + " " if note else "") + (f"own TX heard: {m.get('freq_mhz')} MHz, SNR {m.get('snr')} dB, "
                                                   f"DT {m.get('dt')} s, drift {m.get('drift')} (not stored)")
    sid = db.add_slot(slot, band, dial, rc["name"], bc["antenna"], bc["gain_db"], rec["rate"], rec["peak"], rec["rms_db"], status, note)
    n = db.add_spots(sid, slot, band, spots) if spots else 0
    return n, note


class RadioWorker(threading.Thread):
    def __init__(self, rcfg, jobs, state, stop, make_radio):
        super().__init__(name="radio-" + rcfg["name"], daemon=True)
        self.rcfg, self.jobs, self.state, self.stop_ev, self.make_radio = rcfg, jobs, state, stop, make_radio

    def sleep_until(self, t):
        while not self.stop_ev.is_set() and time.time() < t:
            time.sleep(min(0.5, max(0.0, t - time.time())))

    def run(self):
        name = self.rcfg["name"]
        i, backoff, last_rot = 0, 10, None
        radio = None
        while not self.stop_ev.is_set():
            try:
                if radio is None:
                    self.state.set(name, state="opening", band=None)
                    radio = self.make_radio(self.rcfg)
                    radio.open()
                    backoff = 10
                slot = next_slot(time.time() + MIN_LEAD_S)
                rot = wsm_config.rotation_at(self.rcfg, slot)          # rotation by time of day (radios[].schedule)
                if rot != last_rot:
                    if last_rot is not None:
                        log(f"[{name}] schedule: rotation is now {'>'.join(rot)}")
                    last_rot, i = rot, 0
                band = rot[i % len(rot)]
                i += 1
                dial = wsm_config.BANDS[band]
                self.state.set(name, state="waiting", band=band, next_ts=slot)
                self.sleep_until(slot - 3)
                if self.stop_ev.is_set():
                    break
                self.state.set(name, state="listening", band=band, next_ts=slot + SLOT_S)
                rec = radio.record(band, dial, slot, RECORD_S)
                self.jobs.put(dict(rcfg=self.rcfg, band=band, dial=dial, slot=slot, rec=rec))
                if rec.get("note"):
                    log(f"[{name}] {band}: {rec['note']}")
            except Exception as e:
                log(f"[{name}] error: {type(e).__name__}: {e}")
                self.state.set(name, state="error")
                try:
                    if radio:
                        radio.close()
                except Exception:
                    pass
                radio = None
                self.sleep_until(time.time() + backoff)
                backoff = min(backoff * 2, 300)
        try:
            if radio:
                radio.close()
        except Exception:
            pass
        self.state.set(name, state="stopped")


def decode_loop(cfg, db, jobs, stop):
    pool = None
    if cfg.get("decode_process", True):
        try:
            pool = DecodePool(cfg)
        except Exception as e:
            log(f"decode process not available ({type(e).__name__}: {e}): decoding in a thread")
    while not (stop.is_set() and jobs.empty()):
        try:
            job = jobs.get(timeout=1)
        except queue.Empty:
            continue
        t = time.time()
        try:
            n, note = process(cfg, db, job, pool)
            log(f"[{job['rcfg']['name']}] {job['band']} slot {time.strftime('%H:%M', time.gmtime(job['slot']))}Z: "
                f"{n} spot(s), peak {job['rec']['peak'] * 100:.0f} %, {time.time() - t:.1f} s" + (f" ({note})" if note else ""))
        except Exception as e:
            log(f"decode error: {type(e).__name__}: {e}")
    if pool:
        pool.close()


def sbm_running():
    try:
        return subprocess.run(["systemctl", "is-active", "--quiet", "sbm.service"], timeout=5).returncode == 0
    except Exception:
        return False


def make_radio_factory(sim):
    def f(rcfg):
        if sim or rcfg.get("driver") == "sim":
            return wsm_capture.SimRadio(rcfg, log)
        return wsm_capture.RspRadio(rcfg, log)
    return f


def main():
    ap = argparse.ArgumentParser(description="WSM - WSPR System Monitor")
    ap.add_argument("-c", "--config", default=os.path.expanduser("~/wsm/wsm.yaml"))
    ap.add_argument("--sim", action="store_true", help="use simulated radios (needs the `sim` section in the config)")
    a = ap.parse_args()
    cfg = wsm_config.load(a.config if os.path.exists(os.path.expanduser(a.config)) else None)
    if sbm_running() and not cfg.get("allow_with_sbm"):
        log("sbm.service is running: SBM and WSM cannot share the RSP2. Stop it first (sudo systemctl stop sbm).")
        return 2
    os.makedirs(cfg["data_dir"], exist_ok=True)
    if not os.path.exists(cfg["decoder"]["path"]):
        log(f"decoder not found: {cfg['decoder']['path']} (run `make` in the decoder directory)")
        return 2
    st = cfg["station"]  # PATCH1
    ll = (float(st["lat"]), float(st["lon"])) if st.get("lat") is not None and st.get("lon") is not None else None
    db = wsm_db.DB(os.path.join(cfg["data_dir"], "wsm.sqlite"), st["grid"], ll)
    db.prune(cfg["keep_days"])
    bands = []
    for r in cfg["radios"]:
        for b in wsm_config.all_bands(r):
            if b not in bands:
                bands.append(b)
    state = wsm_web.State(cfg, bands)
    stop = threading.Event()
    state.sysmon = wsm_sys.start(db, stop)                  # temperature, clock, power, slot coverage (web card + health alerts)
    for s in (signal.SIGINT, signal.SIGTERM):
        signal.signal(s, lambda *_: stop.set())
    if cfg["web"]["enabled"]:
        wsm_web.start(cfg, db, state, log)
    wsm_report.start(cfg, db, stop, log)
    wsm_wsprnet.start(cfg, db, stop, log)
    jobs = queue.Queue()
    workers = [RadioWorker(r, jobs, state, stop, make_radio_factory(a.sim)) for r in cfg["radios"]]
    dec = threading.Thread(target=decode_loop, args=(cfg, db, jobs, stop), name="decode")
    dec.start()
    for w in workers:
        w.start()
    log(f"WSM started: {cfg['station']['callsign']} {cfg['station']['grid']}, radios: "
        + ", ".join(f"{r['name']} {'>'.join(wsm_config.rotation_at(r, time.time()))}" + (" (by schedule)" if r.get("_schedule") else "")
                    for r in cfg["radios"]))
    wsm_health.start(cfg, db, state, stop, log)
    wsm_backup.start(cfg, stop, log)
    last_prune = time.time()
    while not stop.is_set():
        time.sleep(1)
        if time.time() - last_prune > 86400:
            db.prune(cfg["keep_days"])
            last_prune = time.time()
    log("stopping...")
    for w in workers:
        w.join(timeout=15)
    dec.join(timeout=130)
    return 0


if __name__ == "__main__":
    sys.exit(main())
