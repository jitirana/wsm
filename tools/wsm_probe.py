#!/usr/bin/env python3
"""wsm_probe.py - what sample rate does the RSP2 really deliver?   Stop WSM and SBM first, then:  python3 wsm_probe.py
For each requested rate: the rate the driver reports, the rate seen from the block arrival times (after a 4 s start-up),
the input level, and the strongest line of the spectrum (with the PU2TKN TX on, it should show near -10000 Hz)."""
import sys, time
import numpy as np
import SoapySDR
from SoapySDR import SOAPY_SDR_RX, SOAPY_SDR_CF32

DIAL = 14095600
OFFSET = 10000
rates = [float(a) for a in sys.argv[1:]] or [62500, 125000, 250000, 500000, 2000000]
dev = SoapySDR.Device(dict(driver="sdrplay"))
print("antennas:", dev.listAntennas(SOAPY_SDR_RX, 0))
try:
    print("rates offered:", [int(r) for r in dev.listSampleRates(SOAPY_SDR_RX, 0)])
except Exception as e:
    print("listSampleRates:", e)
for req in rates:
    try:
        dev.setSampleRate(SOAPY_SDR_RX, 0, req)
        dev.setFrequency(SOAPY_SDR_RX, 0, DIAL + 1500 + OFFSET)
        dev.setAntenna(SOAPY_SDR_RX, 0, "Antenna A")
        dev.setGainMode(SOAPY_SDR_RX, 0, False)
        dev.setGain(SOAPY_SDR_RX, 0, 30.0)
        got = dev.getSampleRate(SOAPY_SDR_RX, 0)
        st = dev.setupStream(SOAPY_SDR_RX, SOAPY_SDR_CF32)
        mtu = int(dev.getStreamMTU(st))
        dev.activateStream(st)
        buf = np.empty(mtu, np.complex64)
        t_arr, n_cum, chunks, tot, t0 = [], [], [], 0, time.time()
        while time.time() - t0 < 14:
            r = dev.readStream(st, [buf], mtu, timeoutUs=500000)
            if r.ret > 0:
                tot += r.ret
                t_arr.append(time.time()); n_cum.append(tot); chunks.append(buf[:r.ret].copy())
        dev.deactivateStream(st); dev.closeStream(st)
        t_arr, n_cum = np.array(t_arr), np.array(n_cum, float)
        k = t_arr > t_arr[0] + 4
        meas = 1.0 / np.polyfit(n_cum[k], t_arr[k], 1)[0]
        x = np.concatenate(chunks)[int(got * 4):]
        n = min(x.size, 1 << 18)
        sp = np.fft.fftshift(np.fft.fft(x[:n] * np.hanning(n)))
        fr = np.fft.fftshift(np.fft.fftfreq(n, 1 / got))
        pw = 20 * np.log10(np.abs(sp) + 1e-12)
        i = int(np.argmax(pw))
        print(f"requested {req:>9.0f}  driver {got:>11.1f}  arrival {meas:>11.1f}  ratio {meas/got:.4f}  mtu {mtu}  "
              f"peak {max(abs(x.real).max(), abs(x.imag).max())*100:.0f} %  strongest line {fr[i]:+.0f} Hz ({pw[i]-np.median(pw):.0f} dB over median)")
    except Exception as e:
        print(f"requested {req:.0f}: {type(e).__name__}: {e}")
