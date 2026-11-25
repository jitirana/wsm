# WSJT-X wsprd (reference decoder)

`wsprd.c`, `wsprd_utils.*`, `fano.*`, `jelinek.*`, `nhash.*`, `tab.c`, `metric_tables.c` and `wsprsim_utils.*` are copied unmodified
from `lib/wsprd/` of WSJT-X (https://github.com/saitohirga/WSJT-X, master, retrieved 2026-11-24), GPL-3.0, Copyright Joe Taylor K1JT,
Steven Franke K9AN and the WSJT-X team (see decoder/LICENSE.GPL-3.0). `osd_stub.c` is the only file written for WSM.

Why: the decoder in `../wsprd/` (from rtlsdr-wsprd, based on the 2015 wsprd) lacks the block demodulation and the candidate search
of today's WSJT-X. With the same simulated signal and noise it needs about 5 dB more SNR for 90 % decodes (see CHANGELOG).

Build: `make -C decoder` produces `decoder/wsprd_wsjtx`. It reads the same 375 S/s IQ slot as `wsm_decode` (as a `.c2` file, written by
WSM) and prints one line per spot: `name SNR DT FREQ_MHz DRIFT CALL [LOC] PWR`.
