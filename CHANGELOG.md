# Changelog

## Unreleased
- Landing page: the first ADS-B card shows the different aircraft heard in the last 24 h (with "N in range now" below).
  `tools/wsm_adsb24.py`, run every minute by `deploy/wsm-adsb24.timer`, keeps the count in `adsb24.json`; both the
  lighttpd copy and `/api/landing` (WSM) read it.
- Station landing page without WSM: `tools/wsm_landing_snapshot.py` writes a static copy (page, favicon, `landing.json` with
  the texts and the best DX ever) for lighttpd (`deploy/lighttpd-station.conf`, one host name only). When `/api/landing` does
  not answer, the page reads `landing.json`, says that the WSPR monitor is temporarily unavailable, hides the 24 h WSPR
  cards and the monitor buttons, and reads the ADS-B numbers live from tar1090's `/tar1090/data/aircraft.json`.
- Content-Security-Policy: scripts no longer run inline unless they are the page's own `<script>` blocks, allowed one by one
  by their SHA-256 (computed when the page is served), so an injected `<script>` or `onclick=` is refused by the browser.
  Also `object-src 'none'`, `base-uri 'none'`, `form-action 'self'`, `frame-ancestors 'none'`, `connect-src 'self'`.
  Inline styles stay allowed. Covered by `wsm_selftest.py`.
- Login and log out: a `/login` form asks for the key and sets the session cookie, so the key no longer travels in the
  address. The page header has **log out** (this browser) and **log out everywhere** (a new session: every old cookie stops
  working). The session comes from the token and a random value in `data/web_session`. Old `/?key=TOKEN` links lead to the
  form; `web.url_key: yes` accepts them again. Scripts use the `X-WSM-Key` header and get the answer directly. Existing
  cookies end with this update: log in once at `/login`. In private mode (`public: no`) the page itself opens the form.
- Web server limits: a connection that stays silent for 15 s is closed (request line, headers, body or idle keep-alive),
  and at most 48 connections are served at once (the next ones get a short 503), so stuck clients cannot pile up threads
  on the Pi. After too many wrong keys only key attempts are refused (429); the public view and a valid cookie keep
  working. Numeric parameters such as `inf` or `1e309` fall back to the default instead of an error. The `Server` header
  no longer shows the Python version, and other HTTP methods answer 405. CSV cells that a spreadsheet would run as a
  formula get a leading `'`. Covered by `wsm_selftest.py` ("web limits").
- Narrow spurs in the WSPR window (roadmap item 8): every slot is searched for steady carriers (`wsm_dsp.find_spurs`): 0.37 Hz
  bins, and per bin the median over the 2.7 s frames of the slot, so only a signal present the whole slot counts; a carrier must
  also hold 70 % of its energy within +-1 Hz, which a WSPR signal (4 tones over 6 Hz) never does. Simulated slots: no false spur in
  200 slots of noise or 300 slots with 1-5 WSPR signals from -28 to +15 dB; carriers found from about -29 dB (2500 Hz) up,
  frequency within 0.02 Hz, level within 0.5 dB. ~3 ms per slot. Saved in the new table `spurs` (RF frequency, offset, SNR);
  "Narrow spurs in the WSPR window" in Health history (key only) groups them by band and frequency with the share of the slots
  of the band in which each one was seen; the waterfall marks them at the bottom ("spur", key only). `/api/spurs?hours=`.
- Thermal health per day (roadmap item 7): the computer history also saves the share of the samples in which the Raspberry Pi
  firmware slowed the CPU (`vcgencmd get_throttled` bits 1-3: frequency capped, throttled, soft temperature limit; column
  `syshist.capped_pct`, added to older databases at start). A "CPU slowdowns per day" table in Health history (key only, local days,
  last 14) shows the hours recorded, mean and highest temperature, minutes slowed by the firmware, below 1000 MHz and with
  under-voltage, the slots, the short slots (samples lost) and how many of these overlapped a slowdown. `/api/thermal?days=`.
- Panels from "Last slot" to "Heard by others, not by me" can be minimized: a small arrow before the title (or a click on the title) folds the
  panel to its header line and opens it again. The choice is kept in this browser (localStorage; another browser or a private window starts
  with everything open). The map and the health charts are redrawn when they open again.
- **Before/after of an event.** A **Δ** button beside each event in Health history shows, per band, the mean noise before the event against the
  mean after it (the first `settle` minutes are skipped, default 10, adjustable, so a lamp or a power supply can settle; both windows stop at the
  neighbouring events), the change in dB, the change of the stream power, and whether it is larger than twice the standard error of the slots
  ("clear change" or "within the normal scatter"). `api/effect?id=..&before=30&settle=10&after=30` (never public). Tested with a synthetic step.
- **Noise shifts found by themselves.** Under the events, "Noise shifts found" lists level changes of 1.5 dB or more (median of 4 slots after a
  point against 4 before, in runs of the same band without a gap of more than 30 minutes, at least 3 times the scatter; one per 20 minutes and
  band). A shift that follows an event is tagged with it; the others have a **mark** button that prepares an event at that time.
  `api/steps?hours=..` (never public). A flat band and a band with a long gap are not reported (tested).
- The noise and stream charts show one decimal on the axis when the range is under 8 dB, and one decimal in the hover (it was rounded to whole dB).
- **Events on the charts.** Health history has a text field, an optional time and a "+ mark event" button: what you did ("LED on", "fan off", "cable moved")
  is stored in the new `events` table and drawn as a dashed violet line with its text on every chart; the list under the charts deletes an event
  with the x. Added `POST /api/event` and `POST /api/event/delete` (need the key, never public; a cross-site form cannot call them: SameSite=Strict
  cookie plus a required `X-WSM: 1` header), and `events` in `api/history`. Tested in `wsm_selftest.py`.
- **Noise vs typical hour.** `noise vs typical hour` button: each band's noise minus its median at the same UTC hour over the last 14 days
  (`DB.noise_baseline`, needs 2 days and 8 slots per hour; the median ignores a few odd days), so the daily drift of the band is removed and a local
  source or a change you made stands out. `api/history` carries the `base` table. Tested with a synthetic odd day.
- **Heard by others, not by me.** New `wsm_missed.py` and panel: once an hour it asks wspr.live what the other receivers in the region
  (`missed.radius_deg`, default 6 degrees) decoded on each band over the last `missed.days` (default 2), paths of at least 500 km, and compares with
  this receiver's own spots in the slots it really listened to. Per band: slots, receivers nearby, heard by them, heard by me, % and the strongest miss
  (best SNR a neighbour had for a transmission I missed); plus the list of misses, strongest first. Propagation cannot explain a miss when a receiver
  a few hundred km away decoded it, so this measures the station. `api/missed` (never public); table `nb_heard`, pruned with `keep_days`;
  `python3 wsm_missed.py --test | --run | --show`; shares the wspr.live settings of `openings.reference`; `missed: {enabled: no}` turns it off.
- Health history has a **1 h** period next to 6 h / 24 h / 7 days / 30 days, to watch the effect of a change right after making it (a lamp, a power
  supply, a cable). The time axis gets a mark every 10 minutes, and the per-band charts break lines and answer the hover within 10 minutes instead of
  30. Same API (`api/history?hours=1`); nothing to migrate. Covered by `wsm_selftest.py`.
- Noise in the WSPR window: each slot now stores `noise_db`, the noise the decoder works against (median of the spectrum over the +-100 Hz of the
  decoder input, so WSPR signals and carriers do not move it), in dB re full scale in 2500 Hz: signal level = noise + SNR. The old `rms_db` is the power of
  the whole 62 kHz stream and moves with strong signals far from the WSPR window (on 24/11 it read -23 dB while a station was decoded at -22 dB, as if the
  noise were 19 dB lower). The Input level card shows both ("WSPR noise -58 dB · stream -25 dB") and Health history has a new chart per band. Older
  databases get the column on start (`ALTER TABLE`); old slots stay empty. Tested in `wsm_selftest.py` (level, signals, gaps, migration).
- Dashboard: the radio card says what is being recorded ("recording 10m"), and the Input level card names the last finished slot with its
  time ("last finished slot: 30m 20:44 UTC · mean -29 dB"). The two cards used to look contradictory, because the level always lags one slot behind.
- WSPRnet upload: a saved `last_id` above the highest spot id in the database (the database was recreated, so the ids start again from 1) used to make
  every new spot look as already sent, and nothing was uploaded, without any message. The service now notices it at start, logs it and restarts from 0
  (spots older than `max_age_h` are still never sent). Covered by `wsm_selftest.py`.
- Decoder: WSM now runs the WSJT-X `wsprd` (new `decoder/wsjtx/`, built as `decoder/wsprd_wsjtx` by `make`) instead of the 2015-era
  decoder of rtlsdr-wsprd (`wsm_decode`, still built and used as fallback). Simulated signals in white noise (same samples for both
  decoders; 100 trials per level, random message, offset and DT, drift 0 or +-1 Hz/min): old decoder 89 % at -24 dB, 64 % at -28 dB,
  19 % at -30 dB; WSJT-X 100 %, 100 %, 66 % (about 5 dB more sensitivity at 90 % decodes). The old decoder also lost almost every
  signal that drifts: at -27 dB with a drift of -1 / +2 Hz/min it decoded 5 % / 0 %, WSJT-X 100 % / 100 % (block demodulation and a
  better candidate search). `decoder.engine: legacy` goes back to the old one. The OSD option of WSJT-X (Fortran) is not built. `debug_keep_iq` works with both.
- Rotation and schedule entries can pick the antenna of the slot: `20m@A, 20m@HIZ`. `tools/wsm_antenna_compare.py` adds a table with the
  SNR difference of the same station heard in adjacent slots on two antennas.
- Peer comparison (`wsm_peers.py`, table `peer_cmp`): my SNR against the median of the receivers at a similar distance that heard the
  same transmitter in the same slot (wspr.live), per band and antenna. Propagation cancels out, so it is a fair way to compare
  antennas, gains or a new location. Shown by `tools/wsm_antenna_compare.py`; `wsm_peers.py --run / --show / --test`.
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
