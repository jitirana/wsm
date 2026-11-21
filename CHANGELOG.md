# Changelog

## 1.8 – "What is WSPR?"
- Public, bilingual page `/about` (English by default, Portuguese button) explaining WSPR to visitors; live station numbers only
  for visitors with the web key. Link in the monitor header.
- Documentation rewritten in English: README, docs/INSTALL, CONFIGURATION, ALERTS, HARDWARE. GPL-3.0 license for the project.

## 1.7
- `store_own_spots` (default `no`): spots of the station's own callsign (TX = RX, also `<CALL>`, `CALL/P`, `XX/CALL`) are not
  stored; their frequency, SNR, DT and drift are kept in the slot note and the log.

## 1.6
- Band rotation by time of day: `schedule` with UTC ranges per radio (wrap over midnight, HH or HH:MM).
- "hide my TX" option on the page and `noown=1` on the API / CSV.

## 1.5
- Computer health (`wsm_sys.py`): temperature, clock, under-voltage, slot coverage; **System** card and `/api/system`.
- New alerts: under-voltage, temperature, samples being lost (`power_events_max`, `temp_max_c`, `coverage_min`).
- Decoder input written to `/dev/shm` (RAM) instead of the SD card.

## 1.4
- "Best DX · all time" card; own callsign never counted as DX; the "all" period works for cards and band table.

## 1.3
- Slot start time from a low percentile of the block arrival delays instead of the median (stable DT on a Raspberry Pi).

## 1.2
- DSP and decoder in a separate low-priority process: on a Pi 3 the decoding held the Python GIL for ~10 s per slot and the
  capture lost samples ("O" overflows).

## 1.1 – Raspberry Pi
- Mixing and decimation in blocks (~85 MB per slot instead of ~370 MB), identical output; capture into one preallocated array.
- `install_pi.sh` and the Raspberry Pi systemd unit (waits for time sync, restarts the SDRplay service as a self-heal step).

## 1.0
- First version on an Ubuntu VM: scheduler, RSP2 capture, WSJT-X decoder, SQLite, web page, WSPRnet upload, daily report,
  health monitor with self-healing, backups.
