"""
wsm_dsp.py - turns the radio stream into what the WSPR decoder wants: 120 s of complex baseband at exactly 375 samples/s,
centred on dial + 1500 Hz, sample 0 = the start of the even UTC minute.

The radio is tuned `offset_hz` above dial + 1500 Hz (so the DC spike of the receiver is far from the WSPR window); here the
stream is mixed back down, low-pass filtered and decimated to about 4 kS/s, then resampled to the exact 375 S/s grid on the
UTC time axis. The real sample rate of the stream is passed in (it can differ from the requested one by a fraction of a percent
and a WSPR frame lasts 110 s, so a 0.1 % error would already shift the last symbols by 0.1 s).
"""
import math

import numpy as np
from scipy import signal
from scipy.interpolate import interp1d

OUT_RATE = 375.0
OUT_SECONDS = 120
OUT_N = int(OUT_RATE * OUT_SECONDS)


def plan_decimation(rate):
    """list of integer decimation stages (each <= 16) that bring `rate` down to between 3000 and ~6000 S/s"""
    q = max(1, int(rate // 3500))
    stages = []
    f = 2
    n = q
    while n > 1 and f <= 13:
        while n % f == 0:
            stages.append(f)
            n //= f
        f += 1
    if n > 1:                      # a prime factor above 13: use a coarser plan instead of one huge filter
        return plan_decimation(rate * (q - 1) / q) if q > 2 else [q]
    out = []
    cur = 1
    for s in sorted(stages, reverse=True):          # merge small factors so there are few stages
        if cur * s <= 16:
            cur *= s
        else:
            out.append(cur)
            cur = s
    if cur > 1:
        out.append(cur)
    return out or [1]


def mix_down(x, rate, offset_hz, t0_index=0):
    """x * exp(+j 2 pi offset n / rate): the RSP is tuned offset_hz ABOVE the signal, so the signal is at -offset; phase kept accurate (float64)"""
    if not offset_hz:
        return x
    n = np.arange(x.size, dtype=np.float64) + t0_index
    ph = (2.0 * np.pi * offset_hz / rate) * n
    return (x * np.exp(1j * ph).astype(np.complex64)).astype(np.complex64)


def decimate_chain(x, rate):
    """integer decimation with anti-alias FIR stages; returns (y, rate_out, total_q)"""
    q_total = 1
    for q in plan_decimation(rate):
        if q <= 1:
            continue
        taps = signal.firwin(24 * q + 1, 0.8 / q, window="hamming")
        x = signal.resample_poly(x, 1, q, window=taps)
        rate /= q
        q_total *= q
    return x.astype(np.complex64), rate, q_total


class _StreamDecimator:
    """
    Block-by-block version of  signal.resample_poly(x, 1, q, window=firwin(24q+1, 0.8/q)):  same output, sample for sample,
    but the input can arrive in blocks, so the whole stream never has to be converted at once (Raspberry Pi: 1 GB of RAM).
    Output k is  sum_j h[j] x[k q + 12 q - j]  (x = 0 outside the stream), k = 0 .. ceil(N/q) - 1.
    """

    def __init__(self, q):
        self.q = q
        self.h = signal.firwin(24 * q + 1, 0.8 / q, window="hamming")
        self.c = 12 * q                                   # group delay of the filter (input samples)
        self.buf = np.zeros(self.c, np.complex128)        # history; buf[0] is input index k0*q - c
        self.n_in = 0
        self.out = []

    def _run(self, final=False):
        q, L = self.q, self.h.size
        if final:
            self.buf = np.concatenate([self.buf, np.zeros(self.c, self.buf.dtype)])
        n_out = (self.buf.size - 1) // q - (L - 1) // q + 1 if self.buf.size >= L else 0
        if n_out <= 0:
            return
        need = (n_out - 1) * q + L                        # input samples used by these outputs
        z = signal.upfirdn(self.h, self.buf[:need], 1, q)[(L - 1) // q:(L - 1) // q + n_out]
        self.out.append(z)
        self.buf = self.buf[n_out * q:]

    def feed(self, block):
        self.n_in += block.size
        self.buf = np.concatenate([self.buf, block])
        self._run()

    def finish(self):
        self._run(final=True)
        y = np.concatenate(self.out) if self.out else np.zeros(0, np.complex128)
        return y[:-(-self.n_in // self.q)]               # ceil(N/q) outputs, like resample_poly


BLOCK = 1 << 18          # input samples converted per block (~4 MB of complex128 temporaries instead of ~370 MB per slot)


def mix_and_decimate(x, rate, offset_hz=0.0, block=BLOCK):
    """same result as  decimate_chain(mix_down(x, rate, offset_hz), rate)  with a small, constant amount of memory"""
    stages = [_StreamDecimator(q) for q in plan_decimation(rate) if q > 1]
    w = (2.0 * np.pi * offset_hz / rate) if offset_hz else 0.0
    out = []
    for a in range(0, x.size, block):
        b = x[a:a + block]
        if w:
            n = np.arange(a, a + b.size, dtype=np.float64)
            b = b * np.exp(1j * (w * n)).astype(np.complex64)      # same phase as mix_down(): float64, from sample 0
        for s in stages:                                          # through the chain as far as this block reaches
            s.feed(b)
            if not s.out:
                b = None
                break
            b = np.concatenate(s.out)
            s.out = []
        if b is not None:
            out.append(b)
    carry = None                                                  # end of stream: flush each stage into the next one
    for s in stages:
        if carry is not None:
            s.feed(carry)
        carry = s.finish()
    if carry is not None:
        out.append(carry)
    q_total = 1
    for s in stages:                                              # stage by stage, exactly as decimate_chain() does
        rate /= s.q
        q_total *= s.q
    y = np.concatenate(out).astype(np.complex64) if out else np.zeros(0, np.complex64)
    return y, rate, q_total


def to_wspr_grid(x, rate, first_index_time, slot_start, offset_hz=0.0):
    """
    x                 complex stream at `rate` S/s
    first_index_time  UTC time (s) of sample 0 of x
    slot_start        UTC time (s) of the even minute: output sample j is the signal at slot_start + j/375
    Returns complex64 array of 45000 samples (zeros where the stream does not cover the slot) and the fraction of the slot covered.
    """
    # mix down (float64 phase from the start of the stream) and decimate, block by block (constant memory)
    y, r2, q = mix_and_decimate(x, rate, offset_hz)
    # band-limit to +-150 Hz before going to 375 S/s (the decimator output is still ~3-6 kS/s wide)
    lp = signal.firwin(401, 150.0 / (r2 / 2.0), window="hamming")
    y = signal.fftconvolve(y, lp, mode="same").astype(np.complex64)
    t_out = slot_start + np.arange(OUT_N) / OUT_RATE
    pos = (t_out - first_index_time) * rate / q          # fractional index into y
    ok = (pos >= 2) & (pos <= y.size - 3)
    out = np.zeros(OUT_N, np.complex64)
    if ok.sum() > 8:
        idx = np.arange(y.size)
        fr = interp1d(idx, y.real, kind="cubic", assume_sorted=True)
        fi = interp1d(idx, y.imag, kind="cubic", assume_sorted=True)
        out[ok] = (fr(pos[ok]) + 1j * fi(pos[ok])).astype(np.complex64)
    return out, float(ok.mean())


def level_stats(x):
    """peak (largest |I| or |Q|, full scale = 1.0) and mean power in dB re full scale of a raw stream"""
    if x.size == 0:
        return 0.0, -200.0
    step = max(1, x.size // 2_000_000)
    s = x[::step]
    peak, acc = 0.0, 0.0
    for a in range(0, s.size, BLOCK):                 # in blocks: a few MB instead of ~40 MB of temporaries
        b = s[a:a + BLOCK]
        peak = max(peak, float(np.abs(b.real).max()), float(np.abs(b.imag).max()))
        acc += float(np.sum(b.real.astype(np.float64) ** 2 + b.imag.astype(np.float64) ** 2))
    p = acc / s.size
    return peak, 10.0 * np.log10(max(p, 1e-20))


def noise_floor_db(iq, rate=OUT_RATE, span_hz=100.0, nfft=256):
    """
    Noise level in the WSPR window, in dB re full scale in 2500 Hz (the bandwidth of the WSPR SNR figure), or None.
    iq: the decoder input (375 S/s, centred on dial + 1500 Hz). Welch spectrum of the whole slot, then the MEDIAN over the
    bins of +-span_hz: a few WSPR signals (4 tones of 1.5 Hz each) or a carrier fill a small part of the bins, so they do not move it.
    Segments without samples (zeros where the stream did not cover the slot) are left out.
    Unlike the "mean" of the stream (62 kHz wide, everything the radio hears), this is what the decoder has to work against:
    signal power in dBFS = noise_floor_db + SNR.
    """
    x = np.asarray(iq, np.complex64)
    hop = nfft // 2
    n = (x.size - nfft) // hop + 1
    if n < 8:
        return None
    seg = np.lib.stride_tricks.as_strided(x, shape=(n, nfft), strides=(x.strides[0] * hop, x.strides[0]))
    seg = seg[np.einsum("ij,ij->i", seg.real, seg.real) + np.einsum("ij,ij->i", seg.imag, seg.imag) > 0]
    if seg.shape[0] < 8:
        return None
    w = np.hanning(nfft).astype(np.float32)
    psd = np.zeros(nfft)                                       # power per bin, full scale^2 / Hz
    for a in range(0, seg.shape[0], 64):
        f = np.fft.fft(seg[a:a + 64] * w, axis=1)
        psd += (np.abs(f) ** 2).sum(axis=0)
    psd = np.fft.fftshift(psd) / (seg.shape[0] * rate * float((w ** 2).sum()))
    freqs = np.fft.fftshift(np.fft.fftfreq(nfft, 1.0 / rate))
    m = psd[np.abs(freqs) <= span_hz]
    if m.size < 8 or not np.isfinite(m).all():
        return None
    med = float(np.median(m))
    return round(10.0 * np.log10(max(med * 2500.0, 1e-30)), 1)


def find_spurs(iq, rate=OUT_RATE, nfft=1024, span_hz=150.0, min_db=6.0, max_n=8):
    """
    Narrow spurs (steady carriers) in the WSPR window of one slot: list of dict(off_hz, snr, above, persist), strongest first,
    or None when the slot has too few samples. off_hz: Hz from the centre of the window (dial + 1500 Hz); snr: dB in 2500 Hz,
    the same scale as the WSPR SNR (carrier power against the noise of the window); above: dB over the typical bin;
    persist: % of the slot in which the carrier stood out.
    How a carrier is told from a WSPR signal: 0.37 Hz bins, and for each bin the MEDIAN over the 2.7 s frames of the slot of
    the strongest of that bin and its two neighbours (a slow drift of the carrier stays inside). A carrier is there the whole
    slot, so its median rises; a WSPR signal hops between 4 tones 1.46 Hz (4 bins) apart, so each bin holds it at most about a
    third of the time and its median stays at the noise. Segments without samples are left out.
    """
    x = np.asarray(iq, np.complex64)
    hop = nfft // 2
    n = (x.size - nfft) // hop + 1
    if n < 12:
        return None
    seg = np.lib.stride_tricks.as_strided(x, shape=(n, nfft), strides=(x.strides[0] * hop, x.strides[0]))
    seg = seg[np.einsum("ij,ij->i", seg.real, seg.real) + np.einsum("ij,ij->i", seg.imag, seg.imag) > 0]
    if seg.shape[0] < 12:
        return None
    w = np.hanning(nfft).astype(np.float32)
    freqs = np.fft.fftshift(np.fft.fftfreq(nfft, 1.0 / rate))
    keep = np.abs(freqs) <= span_hz + 2 * rate / nfft
    p = np.empty((seg.shape[0], int(keep.sum())), np.float64)
    for a in range(0, seg.shape[0], 32):
        f = np.fft.fftshift(np.fft.fft(seg[a:a + 32] * w, axis=1), axes=1)[:, keep]
        p[a:a + 32] = f.real.astype(np.float64) ** 2 + f.imag.astype(np.float64) ** 2
    fk = freqs[keep]
    m3 = np.maximum(p, np.maximum(np.roll(p, 1, axis=1), np.roll(p, -1, axis=1)))
    m3[:, 0], m3[:, -1] = p[:, 0], p[:, -1]
    s = np.median(m3, axis=0)                                   # per bin: level held for most of the slot
    ref = float(np.median(s))
    if not ref > 0:
        return None
    above = 10.0 * np.log10(np.maximum(s, 1e-30) / ref)
    psd = p.mean(axis=0) / (rate * float((w ** 2).sum()))       # mean power per Hz, full scale^2 / Hz
    df = rate / nfft
    inside = np.abs(fk) <= span_hz
    n0 = float(np.median(psd[inside]))                          # noise density of the window
    cand = [k for k in range(1, len(fk) - 1) if inside[k] and above[k] >= min_db and s[k] >= s[k - 1] and s[k] >= s[k + 1]]
    cand = sorted({k - 1 + int(np.argmax(psd[k - 1:k + 2])) for k in cand})   # the median of 3 bins is flat on top: take the real peak
    cand = [k for k in cand if 0 < k < len(fk) - 1]
    kept = []
    for k in sorted(cand, key=lambda k: -psd[k]):
        if all(abs(fk[k] - fk[j]) > 1.5 for j in kept):
            kept.append(k)
    out = []
    for k in kept:
        lo, hi = max(0, k - 3), min(len(fk), k + 4)
        pc = (float(psd[lo:hi].sum()) - (hi - lo) * n0) * df         # carrier power: the bins around the peak minus their noise
        wl, wh = max(0, k - 14), min(len(fk), k + 15)                 # +-5 Hz: the whole width of a WSPR signal around the peak
        pw = (float(psd[wl:wh].sum()) - (wh - wl) * n0) * df
        if pw <= 0 or pc / pw < 0.7:                                  # energy spread over several Hz: a WSPR signal, not a carrier
            continue
        a, b, c = (10.0 * math.log10(max(float(v), 1e-30)) for v in psd[k - 1:k + 2])
        d = 0.5 * (a - c) / (a - 2 * b + c) if (a - 2 * b + c) < 0 else 0.0   # peak between bins (parabola through 3 bins)
        out.append(dict(off_hz=round(float(fk[k]) + max(-0.5, min(0.5, d)) * df, 2), snr=round(float(10.0 * np.log10(max(pc, 1e-30) / (n0 * 2500.0))), 1),
                        above=round(float(above[k]), 1), persist=round(100.0 * float(np.mean(m3[:, k] > 2.0 * ref)))))
        if len(out) >= max_n:
            break
    return out
