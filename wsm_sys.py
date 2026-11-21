"""
wsm_sys.py - health of the computer itself: temperature, CPU clock, power (under-voltage) and the sample coverage of the slots.

A thread samples every `sample_s` seconds (default 10) and keeps the last hour. On a Raspberry Pi it uses `vcgencmd`
(get_throttled / measure_temp / measure_clock); elsewhere it falls back to /sys (temperature and clock only, no power data).
Why it matters: an under-voltage makes the Pi firmware drop the CPU to 600 MHz for a few seconds, the SDRplay API then
falls behind and silently loses samples ("slot covered 80 %"), and the decoder fails. That took days to find by hand.

snapshot() -> dict for the web page (/api/system) and the health monitor:
  pi, temp_c, temp_max_1h, clock_mhz, clock_max_mhz, uv_now, uv_pct_1h, uv_events_1h, low_clock_pct_1h,
  throttled_hex, coverage_1h (%), slots_1h, partial_1h, samples_1h
"""
import re
import shutil
import subprocess
import threading
import time
from collections import deque

LOW_CLOCK_MHZ = 1000        # below this the CPU is being held down (600 MHz = under-voltage/throttling); a 3B+ at 60 C runs 1200
WINDOW_S = 3600


def _run(args):
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=5).stdout.strip()
    except Exception:
        return ""


class SysMon:
    def __init__(self, db=None, sample_s=10.0, clock=time.time):
        self.db, self.sample_s, self.clock = db, float(sample_s), clock
        self.vc = shutil.which("vcgencmd") is not None
        self.samples = deque()          # (ts, temp_c, clock_mhz, throttled_int or None)
        self.lock = threading.Lock()
        self.pi = None
        try:
            self.pi = open("/proc/device-tree/model").read().strip("\x00 \n")
        except OSError:
            pass

    # ----- one reading -----
    def read(self):
        temp = clk = thr = None
        if self.vc:
            m = re.search(r"([\d.]+)", _run(["vcgencmd", "measure_temp"]))
            temp = float(m.group(1)) if m else None
            m = re.search(r"=(\d+)", _run(["vcgencmd", "measure_clock", "arm"]))
            clk = int(m.group(1)) / 1e6 if m else None
            m = re.search(r"=(0x[0-9a-fA-F]+)", _run(["vcgencmd", "get_throttled"]))
            thr = int(m.group(1), 16) if m else None
        if temp is None:
            try:
                temp = int(open("/sys/class/thermal/thermal_zone0/temp").read()) / 1000.0
            except (OSError, ValueError):
                pass
        if clk is None:
            try:
                clk = int(open("/sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq").read()) / 1000.0
            except (OSError, ValueError):
                pass
        return temp, clk, thr

    def sample(self):
        now = self.clock()
        temp, clk, thr = self.read()
        with self.lock:
            self.samples.append((now, temp, clk, thr))
            while self.samples and now - self.samples[0][0] > WINDOW_S:
                self.samples.popleft()

    # ----- slot coverage from the database -----
    def coverage(self, since):
        if self.db is None:
            return None, 0, 0
        with self.db.lock:
            rows = self.db.con.execute("SELECT status, note FROM slots WHERE ts>=?", (int(since),)).fetchall()
        if not rows:
            return None, 0, 0
        tot, partial = 0.0, 0
        for status, note in rows:
            m = re.search(r"covered (\d+)", note or "")
            if m:
                c = float(m.group(1))
            elif status == "error" and "no samples" in (note or ""):
                c = 0.0
            else:
                c = 100.0
            partial += c < 100
            tot += c
        return tot / len(rows), len(rows), partial

    # ----- summary -----
    def snapshot(self):
        now = self.clock()
        with self.lock:
            s = list(self.samples)
        last = s[-1] if s else (None, None, None, None)
        temps = [x[1] for x in s if x[1] is not None]
        clks = [x[2] for x in s if x[2] is not None]
        thr = [x[3] for x in s if x[3] is not None]
        uv = [(t & 1) != 0 for t in thr]
        events = sum(1 for a, b in zip(uv, uv[1:]) if b and not a) + (1 if uv and uv[0] else 0)
        cov, nslots, partial = self.coverage(now - WINDOW_S)
        return dict(
            pi=self.pi, power_data=self.vc,
            temp_c=last[1], temp_max_1h=max(temps) if temps else None,
            clock_mhz=round(last[2]) if last[2] else None, clock_max_mhz=round(max(clks)) if clks else None,
            throttled_hex=hex(last[3]) if last[3] is not None else None,
            uv_now=bool(last[3] & 1) if last[3] is not None else None,
            uv_pct_1h=round(100.0 * sum(uv) / len(uv), 1) if uv else None,
            uv_events_1h=events if uv else None,
            low_clock_pct_1h=round(100.0 * sum(1 for c in clks if c < LOW_CLOCK_MHZ) / len(clks), 1) if clks else None,
            coverage_1h=round(cov, 1) if cov is not None else None, slots_1h=nslots, partial_1h=partial,
            samples_1h=len(s), now=int(now))

    def run(self, stop):
        while not stop.is_set():
            try:
                self.sample()
            except Exception:
                pass
            stop.wait(self.sample_s)


def start(db, stop, sample_s=10.0):
    m = SysMon(db, sample_s)
    threading.Thread(target=m.run, args=(stop,), name="sysmon", daemon=True).start()
    return m


if __name__ == "__main__":                  # quick look: python3 wsm_sys.py
    m = SysMon()
    for _ in range(3):
        m.sample()
        time.sleep(1)
    import json
    print(json.dumps(m.snapshot(), indent=1))
