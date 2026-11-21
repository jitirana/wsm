#!/usr/bin/env python3
"""
wsm_testsignal.py - writes a synthetic WSPR recording (what the receiver chain hands to the decoder) to test WSM without the radio.

  complex float32 IQ, 375 samples/s, 120 s, baseband centred on dial + 1500 Hz, one WSPR transmission plus white noise.

  ./wsm_testsignal.py out.iq --msg "PU2TKN HI03 10" --snr -18 --offset 20 --dt 0.5

--offset is the tone frequency relative to 1500 Hz (the WSPR window is +-100 Hz), --snr is the usual WSPR SNR (dB in 2500 Hz),
--drift is in Hz/min, --invert swaps I/Q (mirror image: the sign convention of the receiver chain).
The 162 channel symbols come from the encoder of the wsprd sources (wsm_symbols), so the message is a real WSPR frame.
"""
import argparse
import os
import subprocess
import sys

import numpy as np

FS = 375.0
SYM_SAMPLES = 256                      # 8192 samples at 12 kHz = 0.6827 s -> exactly 256 samples at 375 sps
TONE_STEP = 12000.0 / 8192.0           # 1.4648 Hz


def channel_symbols(msg, exe):
    out = subprocess.run([exe, msg], capture_output=True, text=True, check=True).stdout.strip()
    if len(out) != 162:
        raise RuntimeError(f"encoder returned {len(out)} symbols")
    return np.array([int(c) for c in out])


def make(msg, snr_db, offset_hz, dt_s, drift_hz_min, exe, seed=1, invert=False, nsec=120):
    rng = np.random.default_rng(seed)
    n = int(nsec * FS)
    sym = channel_symbols(msg, exe)
    t = np.arange(n) / FS
    # instantaneous frequency of the tone (symbol index from the time since the start of the transmission)
    t0 = 1.0 + dt_s                                  # the transmission starts 1 s after the even minute
    k = np.floor((t - t0) / (SYM_SAMPLES / FS)).astype(int)
    on = (k >= 0) & (k < 162)
    f = np.zeros(n)
    f[on] = offset_hz + TONE_STEP * (sym[k[on]] - 1.5) + drift_hz_min * (t[on] - t0) / 60.0
    ph = 2 * np.pi * np.cumsum(f) / FS
    amp = np.sqrt(10 ** (snr_db / 10.0) * 2500.0 / FS)       # noise sigma = 1 per complex sample (power 1 over 375 Hz)
    sig = np.where(on, amp * np.exp(1j * ph), 0)
    noise = (rng.standard_normal(n) + 1j * rng.standard_normal(n)) / np.sqrt(2)
    x = sig + noise
    if invert:
        x = np.conj(x)
    return x.astype(np.complex64)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("out")
    ap.add_argument("--msg", default="PU2TKN HI03 10")
    ap.add_argument("--snr", type=float, default=-15.0)
    ap.add_argument("--offset", type=float, default=0.0)
    ap.add_argument("--dt", type=float, default=0.0)
    ap.add_argument("--drift", type=float, default=0.0)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--invert", action="store_true")
    ap.add_argument("--encoder", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "decoder", "wsm_symbols"))
    a = ap.parse_args()
    x = make(a.msg, a.snr, a.offset, a.dt, a.drift, a.encoder, a.seed, a.invert)
    x.tofile(a.out)
    print(f"{a.out}: {x.size} samples, '{a.msg}', SNR {a.snr:g} dB, offset {a.offset:+g} Hz, dt {a.dt:+g} s, drift {a.drift:+g} Hz/min")


if __name__ == "__main__":
    sys.exit(main())
