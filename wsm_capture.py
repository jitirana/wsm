"""
wsm_capture.py - radios. Every radio has the same interface:

    radio.open()
    rec = radio.record(band, dial_hz, slot_start, seconds)   -> dict(x, rate, t0, peak, rms_db, gaps, note)
        x      complex64 stream centred on dial + 1500 Hz + offset_hz
        rate   real sample rate of the stream (S/s)
        t0     UTC time (s) of x[0]
    radio.close()

RspRadio drives an SDRplay RSP2 through SoapySDR (SoapySDRPlay3). SimRadio makes a stream out of synthetic WSPR transmissions,
to test everything else without a radio.
"""
import time

import numpy as np

import wsm_dsp

ANT = {"a": "Antenna A", "b": "Antenna B", "hiz": "Hi-Z"}


def antenna_name(s):
    k = str(s).strip().lower().replace("-", "").replace("_", "").replace(" ", "")
    k = {"antennaa": "a", "antennab": "b"}.get(k, k)
    if k not in ANT:
        raise SystemExit(f"invalid antenna {s!r} (use A, B or HIZ)")
    return ANT[k]


class RspRadio:
    PRE_ROLL_S = 2.0          # the stream is started this long before the slot, so the first samples are settled

    def __init__(self, cfg, log=print):
        self.cfg, self.log = cfg, log
        self.name = cfg["name"]
        self.dev = self.stream = None
        self.rate_req = float(cfg["sample_rate"])

    def open(self):
        import SoapySDR
        from SoapySDR import SOAPY_SDR_RX
        self.S, self.RX = SoapySDR, SOAPY_SDR_RX
        c = self.cfg
        dev = SoapySDR.Device(dict(driver=c["driver"]))
        self.dev = dev
        dev.setSampleRate(SOAPY_SDR_RX, 0, self.rate_req)
        self.rate_nom = float(dev.getSampleRate(SOAPY_SDR_RX, 0))
        if abs(self.rate_nom / self.rate_req - 1) > 0.01:
            self.log(f"[{self.name}] asked for {self.rate_req:g} S/s, the driver gives {self.rate_nom:g} S/s")
        if c.get("if_bandwidth"):
            dev.setBandwidth(SOAPY_SDR_RX, 0, float(c["if_bandwidth"]))
        if c["driver"] == "sdrplay":
            dev.setGainMode(SOAPY_SDR_RX, 0, False)
            for key in ("biasT_ctrl", "rfnotch_ctrl", "extref_ctrl"):
                try:
                    dev.writeSetting(key, "false")
                except Exception:
                    pass
        if c.get("ppm"):
            try:
                dev.setFrequencyCorrection(SOAPY_SDR_RX, 0, float(c["ppm"]))
            except Exception as e:
                self.log(f"[{self.name}] ppm correction not applied: {e}")
        self.log(f"[{self.name}] radio open, {self.rate_nom:g} S/s")

    def close(self):
        try:
            if self.stream is not None:
                self.dev.deactivateStream(self.stream)
                self.dev.closeStream(self.stream)
        except Exception:
            pass
        self.stream = None
        self.dev = None

    def _band_cfg(self, band):
        c = dict(self.cfg)
        c.update((self.cfg.get("bands") or {}).get(band) or {})
        return c

    def record(self, band, dial_hz, slot_start, seconds):
        S, RX, dev = self.S, self.RX, self.dev
        bc = self._band_cfg(band)
        center = dial_hz + 1500.0 + float(bc["offset_hz"])
        dev.setAntenna(RX, 0, antenna_name(bc["antenna"]))
        dev.setFrequency(RX, 0, center)
        dev.setGain(RX, 0, float(bc["gain_db"]))
        self.stream = dev.setupStream(RX, S.SOAPY_SDR_CF32)
        mtu = max(1024, int(dev.getStreamMTU(self.stream)))
        wait = slot_start - self.PRE_ROLL_S - time.time()
        if wait > 0:
            time.sleep(wait)
        dev.activateStream(self.stream)
        end_t = slot_start + seconds
        t_arr, n_cum, gaps, timeouts = [], [], 0, 0
        total = 0
        # PI: one preallocated array, filled in place (no list of chunks + concatenate = no second copy of the slot in RAM)
        cap = int(self.rate_nom * (seconds + self.PRE_ROLL_S + 4)) + mtu
        xbuf = np.empty(cap, np.complex64)
        while True:
            if total + mtu > xbuf.size:                     # more samples than expected: grow (rare)
                xbuf = np.concatenate([xbuf, np.empty(xbuf.size // 4 + mtu, np.complex64)])
            r = dev.readStream(self.stream, [xbuf[total:total + mtu]], mtu, timeoutUs=500_000)
            now = time.time()
            if r.ret > 0:
                total += r.ret
                t_arr.append(now)
                n_cum.append(total)
            elif r.ret == S.SOAPY_SDR_OVERFLOW:
                gaps += 1
            elif r.ret == S.SOAPY_SDR_TIMEOUT:
                timeouts += 1
                if timeouts > 20:
                    break
            if now >= end_t:
                break
        try:
            dev.deactivateStream(self.stream)
            dev.closeStream(self.stream)
        except Exception:
            pass
        self.stream = None
        if total < 10 * mtu:
            return dict(x=np.zeros(0, np.complex64), rate=self.rate_nom, t0=slot_start, peak=0.0, rms_db=-200.0, gaps=gaps,
                        note="no samples")
        x = xbuf[:total]
        del xbuf
        t_arr, n_cum = np.array(t_arr), np.array(n_cum, float)
        # PATCH1: nominal rate; arrival times only used (second half of the recording, after the start-up burst) for the time stamp
        rate = self.rate_nom
        h = len(t_arr) // 2
        note = ""
        if h > 20:
            slope = float(np.polyfit(n_cum[h:], t_arr[h:], 1)[0])
            meas = 1.0 / slope if slope > 0 else rate
            if abs(meas / rate - 1) > 5e-4:
                note = f"arrival-time rate {meas:.1f} S/s differs from {rate:.1f} (ignored)"
        # PI: (arrival time - samples so far / rate) = start of the stream + the delay of that block. The delay can only be
        # positive and on the Pi it jitters by ~0.5 s (the RSP2 hands over ~1 s blocks), so the median moved DT by up to 0.6 s
        # from slot to slot. A low percentile picks the blocks that arrived with the least delay: stable from slot to slot;
        # the constant part of the delay is then removed with clock_offset_s (calibrated with the GPS-timed TX).
        lat = (t_arr - n_cum / rate)[h:] if h > 20 else (t_arr - n_cum / rate)
        t0 = float(np.percentile(lat, 5)) + float(bc.get("clock_offset_s", 0.0))
        peak, rms = wsm_dsp.level_stats(x)
        if gaps:
            note = (note + " " if note else "") + f"{gaps} overflow(s)"
        return dict(x=x, rate=rate, t0=t0, peak=peak, rms_db=rms, gaps=gaps, note=note)


class SimRadio:
    """synthetic stream: sim = {band: [ {msg, snr, offset, dt, drift}, ... ]} -> noise plus those WSPR transmissions"""

    def __init__(self, cfg, log=print):
        self.cfg, self.log = cfg, log
        self.name = cfg["name"]
        self.sim = cfg.get("sim", {})
        self.rate_nom = float(cfg["sample_rate"])
        self.rate_err_ppm = float(cfg.get("sim_rate_error_ppm", 0.0))
        self.encoder = cfg.get("sim_encoder")
        self.n = 0

    def open(self):
        self.log(f"[{self.name}] SIMULATED radio, {self.rate_nom:g} S/s")

    def close(self):
        pass

    def record(self, band, dial_hz, slot_start, seconds):
        import wsm_testsignal as T
        from fractions import Fraction
        from scipy import signal
        self.n += 1
        pre = 2.0
        base = np.zeros(int((120 + pre + 2) * 375), np.complex64)
        rng = np.random.default_rng(1000 + self.n)
        base += ((rng.standard_normal(base.size) + 1j * rng.standard_normal(base.size)) / np.sqrt(2)).astype(np.complex64)
        for k, s in enumerate(self.sim.get(band, [])):
            sig = T.make(s["msg"], s.get("snr", -15), s.get("offset", 0.0), s.get("dt", 1.0), s.get("drift", 0.0), self.encoder,
                         seed=7 + self.n + k)
            noise = T.make("PU2TKN HI03 0", -99, 0, 0, 0, self.encoder, seed=0)          # (-99 dB: noise only, unit variance)
            base[int(pre * 375):int(pre * 375) + sig.size] += (sig - noise)
        fr = Fraction(int(self.rate_nom), 375)
        up = signal.resample_poly(base, fr.numerator, fr.denominator).astype(np.complex64)
        R = self.rate_nom * (1 + self.rate_err_ppm * 1e-6)
        off = float(self.cfg["offset_hz"])
        n = np.arange(up.size, dtype=np.float64)
        x = (up * np.exp(-2j * np.pi * off * n / R)).astype(np.complex64) * np.float32(0.05)
        peak, rms = wsm_dsp.level_stats(x)
        return dict(x=x, rate=R, t0=slot_start - pre, peak=peak, rms_db=rms, gaps=0, note="simulated")
