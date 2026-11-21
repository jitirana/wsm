#!/usr/bin/env python3
"""wsm_probe3.py - measure the REAL sample rate of the RSP2 stream using the PU2TKN TX itself (no clock involved).
A WSPR transmission lasts exactly 162*8192/12000 = 110.592 s. We record ~130 s around one even-minute slot, find the narrow line that
switches on and off, count the samples between the two edges and divide by 110.592.
Stop WSM/SBM first, TX must be running (GPS lock).   Usage: python3 wsm_probe3.py        (takes about 2.5 min)
       python3 wsm_probe3.py --selftest   (synthetic check of the analysis, no radio)"""
import sys, time
import numpy as np

TX_S = 162 * 8192 / 12000.0
W = 8192


def analyze(x, rate):
    n = len(x) // W
    X = x[:n * W].reshape(n, W) * np.hanning(W).astype(np.float32)
    S = np.fft.fftshift(np.abs(np.fft.fft(X, axis=1)) ** 2, axes=1)
    f = np.fft.fftshift(np.fft.fftfreq(W, 1 / rate))
    ok = (np.abs(f) > 1500) & (np.abs(f) < 26000)
    per = rate / W                                    # windows per second (nominal label)
    off = np.r_[0:int(3 * per), n - int(5 * per):n]   # first 3 s and last 5 s: TX not transmitting
    mid = np.arange(int(20 * per), int(95 * per))     # well inside the transmission
    A = 10 * np.log10(S[mid].mean(0) / (S[off].mean(0) + 1e-20) + 1e-20)
    A[~ok] = -99
    k = int(np.argmax(A))
    s = 10 * np.log10(S[:, k - 1:k + 2].sum(1) + 1e-20)
    hi, lo = np.median(s[mid]), np.median(s[off])
    thr = (hi + lo) / 2
    on = s > thr
    i0 = next(i for i in range(n - 3) if on[i] and on[i + 1] and on[i + 2])
    i1 = next(i for i in range(n - 1, 3, -1) if on[i] and on[i - 1] and on[i - 2])
    dN = (i1 - i0 + 1) * W
    return dict(f=f[k], contrast=A[k], on_db=hi - lo, start_idx=i0 * W, dN=dN, R=dN / TX_S)


def report(res, rate_lab, total, wall):
    R = res["R"]
    print(f"line found at {res['f']:+.0f} Hz (nominal axis), {res['contrast']:.0f} dB between TX on and off")
    print(f"TX on-time: {res['dN']} samples  ->  real sample rate {R:.0f} S/s  (labelled {rate_lab:.0f}, ratio {R / rate_lab:.4f})")
    print(f"TX started {res['start_idx'] / R:.1f} s after the first sample; the capture had {total} samples in {wall:.1f} s of wall clock "
          f"({total / wall:.0f} S/s by the clock)")


if "--selftest" in sys.argv:
    lab, act = 62500.0, 81250.0
    rng = np.random.default_rng(1)
    N = int(130 * act)
    x = (rng.standard_normal(N) + 1j * rng.standard_normal(N)).astype(np.complex64)
    t = np.arange(N) / act
    on = (t > 7) & (t < 7 + TX_S)
    tone = 0.25 * np.exp(2j * np.pi * (-10000.0) * t) * on        # true -10 kHz; the label rate makes it look like -7692 Hz
    x += tone.astype(np.complex64)
    report(analyze(x, lab), lab, N, 130.0)
    sys.exit()

import SoapySDR
from SoapySDR import SOAPY_SDR_RX, SOAPY_SDR_CF32
RATE = 62500.0
dev = SoapySDR.Device(dict(driver="sdrplay"))
dev.setSampleRate(SOAPY_SDR_RX, 0, RATE)
dev.setAntenna(SOAPY_SDR_RX, 0, "Antenna A")
dev.setGainMode(SOAPY_SDR_RX, 0, False)
dev.setGain(SOAPY_SDR_RX, 0, 30.0)
dev.setFrequency(SOAPY_SDR_RX, 0, 14097100.0 + 10000.0)
slot = (int(time.time()) // 120 + 1) * 120
if slot - time.time() < 20:
    slot += 120
print(f"waiting for the slot at {time.strftime('%H:%M:%S', time.gmtime(slot))} UTC (starting the capture 5 s before it) ...")
st = dev.setupStream(SOAPY_SDR_RX, SOAPY_SDR_CF32)
mtu = int(dev.getStreamMTU(st))
while time.time() < slot - 5:
    time.sleep(0.2)
t_start = time.time()
dev.activateStream(st)
buf = np.empty(mtu, np.complex64)
chunks, total = [], 0
while time.time() - t_start < 130:
    r = dev.readStream(st, [buf], mtu, timeoutUs=500000)
    if r.ret > 0:
        chunks.append(buf[:r.ret].copy()); total += r.ret
wall = time.time() - t_start
dev.deactivateStream(st); dev.closeStream(st)
x = np.concatenate(chunks)
print("analysing ...")
try:
    report(analyze(x, RATE), RATE, total, wall)
except Exception as e:
    print("could not find the TX on/off pattern:", type(e).__name__, e, "- was the TX transmitting in this slot?")
