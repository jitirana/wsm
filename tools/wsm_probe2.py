#!/usr/bin/env python3
"""wsm_probe2.py - does the TX line move as expected when the tuning changes?  Stop WSM/SBM first. TX must be ON.
Tunes the RSP2 so that the PU2TKN TX (14.0971 MHz) should appear at -4000, -10000 and -16000 Hz, and lists the 4 strongest lines."""
import time
import numpy as np
import SoapySDR
from SoapySDR import SOAPY_SDR_RX, SOAPY_SDR_CF32

RATE = 62500.0
TX = 14097100.0
dev = SoapySDR.Device(dict(driver="sdrplay"))
dev.setSampleRate(SOAPY_SDR_RX, 0, RATE)
dev.setAntenna(SOAPY_SDR_RX, 0, "Antenna A")
dev.setGainMode(SOAPY_SDR_RX, 0, False)
dev.setGain(SOAPY_SDR_RX, 0, 30.0)
print("driver rate", dev.getSampleRate(SOAPY_SDR_RX, 0), " bandwidth", dev.getBandwidth(SOAPY_SDR_RX, 0))
for off in (4000.0, 10000.0, 16000.0):
    dev.setFrequency(SOAPY_SDR_RX, 0, TX + off)
    print(f"\nLO at TX + {off:.0f} Hz -> expected TX line at {-off:+.0f} Hz; reported LO {dev.getFrequency(SOAPY_SDR_RX, 0):.1f}")
    st = dev.setupStream(SOAPY_SDR_RX, SOAPY_SDR_CF32)
    dev.activateStream(st)
    buf = np.empty(65536, np.complex64)
    chunks, tot, t_arr, n_cum, t0 = [], 0, [], [], time.time()
    while time.time() - t0 < 12:
        r = dev.readStream(st, [buf], 65536, timeoutUs=500000)
        if r.ret > 0:
            tot += r.ret; chunks.append(buf[:r.ret].copy()); t_arr.append(time.time()); n_cum.append(tot)
    dev.deactivateStream(st); dev.closeStream(st)
    t_arr, n_cum = np.array(t_arr), np.array(n_cum, float)
    k = t_arr > t_arr[0] + 3
    print(f"  arrival rate {1.0 / np.polyfit(n_cum[k], t_arr[k], 1)[0]:.0f} S/s")
    x = np.concatenate(chunks)[int(RATE * 4):]
    n = 1 << 18
    sp = np.abs(np.fft.fftshift(np.fft.fft(x[:n] * np.hanning(n))))
    fr = np.fft.fftshift(np.fft.fftfreq(n, 1 / RATE))
    db = 20 * np.log10(sp + 1e-12)
    med = np.median(db)
    found = []
    for i in np.argsort(db)[::-1]:
        if all(abs(fr[i] - f) > 30 for f, _ in found):
            found.append((fr[i], db[i] - med))
        if len(found) == 4:
            break
    print("  strongest lines:", ", ".join(f"{f:+.0f} Hz ({d:.0f} dB)" for f, d in found))
