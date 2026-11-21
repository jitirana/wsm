"""
wsm_waterfall.py - the waterfall picture of one slot: what the decoder "saw".

Input: the decoder input of a slot (45000 complex samples at 375 S/s, centred on dial + 1500 Hz, sample 0 = the even minute).
Output: a small PNG, frequency across (+-150 Hz, the whole WSPR window), time downwards (0 at the top, 120 s at the bottom).
A WSPR signal shows as a thin vertical trace about 110 s long; the decoded callsigns are drawn over it by the web page.
Only numpy + zlib (no plotting library on the Raspberry Pi); the picture is kept in RAM, never written to the SD card.
"""
import struct
import zlib

import numpy as np

RATE = 375.0
NFFT = 512             # 1.37 s window -> 0.73 Hz bins: the 4 tones of WSPR (1.46 Hz apart) separate
HOP = 128              # 0.34 s per row -> ~350 rows for the slot
SPAN_HZ = 150.0        # +- around dial + 1500 Hz
RANGE_DB = 30.0        # colour scale: noise floor .. floor + 30 dB

# colour map: noise dark blue, weak signals cyan, strong yellow / red / white
_STOPS = [(0.00, (8, 12, 40)), (0.30, (20, 60, 150)), (0.55, (0, 170, 200)), (0.72, (240, 225, 50)),
          (0.86, (240, 100, 30)), (1.00, (255, 255, 255))]


def _lut():
    x = np.linspace(0, 1, 256)
    out = np.zeros((256, 3), np.uint8)
    for c in range(3):
        out[:, c] = np.interp(x, [s[0] for s in _STOPS], [s[1][c] for s in _STOPS]).astype(np.uint8)
    return out


LUT = _lut()


def png_rgb(img):
    """minimal PNG encoder for an (h, w, 3) uint8 array"""
    h, w, _ = img.shape
    raw = b"".join(b"\x00" + img[y].tobytes() for y in range(h))

    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))


def waterfall(iq, rate=RATE, nfft=NFFT, hop=HOP, span=SPAN_HZ, range_db=RANGE_DB):
    """-> dict(png=bytes, f_lo, f_hi (Hz relative to dial+1500), rows, width, t0, dt) or None.
    Row i shows the signal around t0 + i * dt seconds after the even minute (the page uses that to place the callsigns)."""
    iq = np.asarray(iq, np.complex64)
    if iq.size < nfft * 4:
        return None
    n = (iq.size - nfft) // hop + 1
    win = np.hanning(nfft).astype(np.float32)
    f = np.fft.fftshift(np.fft.fftfreq(nfft, 1.0 / rate))
    keep = np.abs(f) <= span
    rows = np.empty((n, int(keep.sum())), np.float32)
    valid = np.zeros(n, bool)
    for i in range(n):                                     # row by row: tiny memory
        fr = iq[i * hop:i * hop + nfft]
        valid[i] = bool(np.any(fr))
        s = np.fft.fftshift(np.fft.fft(fr * win))[keep]
        rows[i] = 10.0 * np.log10(s.real.astype(np.float64) ** 2 + s.imag.astype(np.float64) ** 2 + 1e-20)
    if not valid.any():
        return None
    floor = float(np.median(rows[valid]))                  # noise level of the slot
    v = np.clip((rows - floor) / range_db, 0.0, 1.0)
    v[~valid] = 0.0                                        # parts of the slot with no samples stay dark
    img = LUT[(v * 255).astype(np.uint8)]
    return dict(png=png_rgb(img), f_lo=float(f[keep][0]), f_hi=float(f[keep][-1]), rows=int(n), width=int(keep.sum()),
                t0=nfft / 2.0 / rate, dt=hop / rate)
