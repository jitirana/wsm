"""
wsm_dsp.py - turns the radio stream into what the WSPR decoder wants: 120 s of complex baseband at exactly 375 samples/s,
centred on dial + 1500 Hz, sample 0 = the start of the even UTC minute.

The radio is tuned `offset_hz` above dial + 1500 Hz (so the DC spike of the receiver is far from the WSPR window); here the
stream is mixed back down, low-pass filtered and decimated to about 4 kS/s, then resampled to the exact 375 S/s grid on the
UTC time axis. The real sample rate of the stream is passed in (it can differ from the requested one by a fraction of a percent
and a WSPR frame lasts 110 s, so a 0.1 % error would already shift the last symbols by 0.1 s).
"""
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
