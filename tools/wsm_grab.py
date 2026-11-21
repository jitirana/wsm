#!/usr/bin/env python3
"""wsm_grab.py - record 12 s of raw IQ from the RSP2 (62.5 kS/s label) with block sizes and arrival times, for analysis.
Stop WSM/SBM first.  Usage: python3 wsm_grab.py   -> writes wsm_grab.npz (about 8 MB); attach it in the chat."""
import time
import numpy as np
import SoapySDR
from SoapySDR import SOAPY_SDR_RX, SOAPY_SDR_CF32

RATE = 62500.0
dev = SoapySDR.Device(dict(driver="sdrplay"))
dev.setSampleRate(SOAPY_SDR_RX, 0, RATE)
dev.setAntenna(SOAPY_SDR_RX, 0, "Antenna A")
dev.setGainMode(SOAPY_SDR_RX, 0, False)
dev.setGain(SOAPY_SDR_RX, 0, 30.0)
dev.setFrequency(SOAPY_SDR_RX, 0, 14107100.0)
info = f"rate {dev.getSampleRate(SOAPY_SDR_RX, 0)} bw {dev.getBandwidth(SOAPY_SDR_RX, 0)} gain {dev.getGain(SOAPY_SDR_RX, 0)}"
st = dev.setupStream(SOAPY_SDR_RX, SOAPY_SDR_CF32)
mtu = int(dev.getStreamMTU(st))
buf = np.empty(mtu, np.complex64)
chunks, sizes, flags, times = [], [], [], []
t0 = time.time()
dev.activateStream(st)
while time.time() - t0 < 12:
    r = dev.readStream(st, [buf], mtu, timeoutUs=500000)
    times.append(time.time() - t0); flags.append(r.flags if r.ret > 0 else -r.ret if r.ret < 0 else 0); sizes.append(r.ret)
    if r.ret > 0:
        chunks.append(buf[:r.ret].copy())
dev.deactivateStream(st); dev.closeStream(st)
x = np.concatenate(chunks)
np.savez_compressed("wsm_grab.npz", x=x, sizes=np.array(sizes), flags=np.array(flags), times=np.array(times), mtu=mtu, info=info)
print(info, "| mtu", mtu, "| blocks", len(sizes), "| samples", x.size, "| wrote wsm_grab.npz")
