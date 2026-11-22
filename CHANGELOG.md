# Changelog

## Unreleased
- One spot per station per slot (`dedup_same_call`, on by default): when the same callsign and locator are decoded several times in a
  slot (the copies a very strong signal leaves at other frequencies, e.g. mains-ripple sidebands) only the best SNR is stored and
  uploaded; the slot note says how many copies were dropped. Hashed callsigns are never merged. The waterfall still labels every decode.
- "Band openings" panel (public): bands x 24 hours. Top row of each band = what this receiver decoded (spots per listened hour,
  hatched = never listened); bottom row = reference from the public WSPRnet database (wspr.live) for the region around the
  station, shown while the own data is thin. New module `wsm_openings.py`, `/api/openings?days=`, tables `ref_openings` and `ref_meta`.
  The reference is one read-only query per band about once a day (box rounded to whole degrees; own callsign excluded); disable with
  `openings: {reference: {enabled: no}}`. Check it with `python3 wsm_openings.py -c wsm.yaml --fetch`.
- `tools/wsm_antenna_compare.py`: compares reception by antenna setting (A / B / HIZ) and gain from the `slots` and `spots`
  tables: slots, spots per hour, median SNR, best DX, input peak and mean power, per band and period.

## 1.14 – Health history
- The computer health is saved every 5 minutes (table `syshist`: temperature, CPU clock, under-voltage).
- "Health history" panel (key only): CPU temperature with the alert level, lowest CPU clock with under-voltage marks, slot
  coverage and noise level per band, for 6 h / 24 h / 7 days / 30 days, values under the pointer. `/api/history?hours=`.

## 1.13 – Public view and station page
- `web.public`: visitors without the key get a read-only view (no health card, no CSV, position only to the 6-character
  locator square); a "public view" tag in the header.
- Station landing page (`landing`, `static/station.html`): WSPR numbers of the last 24 h, ADS-B numbers from readsb
  (`wsm_landing.py`, `adsb.json_dir`), about the station, equipment, links and other projects; served on its own host name.

## 1.12 – Day and night
- Map: the part of the Earth where it is night now is shaded, with the grey line (sunrise / sunset) and the point where the
  Sun is overhead; updated every minute, "night" button to switch it off.

## 1.11
- False decodes with an impossible locator (e.g. `<...> JZZ8XZ`) are dropped and only noted in the slot log.

## 1.10 – Explanations
- Waterfall tabs show how long ago the slot was ("20m · 6 min ago"); the panel title shows local time with UTC beside it.
- A "?" next to every card, panel title and spot-list column opens a short explanation (SNR, DT, drift, power in dBm,
  coverage, input level...). Works with a tap on phones; Esc or a tap elsewhere closes it.

## 1.9 – Waterfall
- "Last slot · waterfall" panel: frequency across (±150 Hz around dial + 1500 Hz), time downwards, one tab per band, the
  decoded callsigns with their SNR drawn on their traces (own TX dashed). Made from the decoder input, kept in RAM only.
- `/api/waterfall` and `/api/waterfall.png?band=`; option `waterfall` (default `yes`).
- The "What is WSPR?" page shows a real slot of this station to visitors who have the web key.

## 1.8.1
- Period labels on the page follow the selector ("last 7 days" instead of "last 168 h").

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
