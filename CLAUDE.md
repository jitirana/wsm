# WSM – WSPR System Monitor

A WSPR receiver and monitor that runs 24/7 on a **Raspberry Pi 3B+** with an **SDRplay RSP2**: it records 2-minute slots,
decodes them (WSJT-X `wsprd`), stores spots in SQLite, uploads to WSPRnet and serves a web page with health, noise and
diagnostics. Hardware and antennas (the station uses a long wire on the Hi-Z port): `docs/HARDWARE.md`; ideas and
their state: `docs/ROADMAP.md`; settings: `docs/CONFIGURATION.md`.

## Talking to the user
- Answer in **Brazilian Portuguese**. Code, comments, commit messages and docs stay in English.
- The user runs every command on the Pi themselves and pastes the output back: give complete, copy-ready commands, one
  per `bash` block, starting with `cd ~/wsm`.
- Every update instruction must say whether the service needs a restart (see the skill `wsm-update-pi`).

## Workflow
1. New work goes on a **branch** (never commit features straight to `main`).
2. Run `python3 wsm_selftest.py` (must end with exit 0) and check the page JavaScript still parses
   (`node --check` on the `<script>` of `wsm_web.PAGE`).
3. Push the branch; the user tests it on the Pi (`git checkout <branch> && git pull` + restart when needed).
4. **Merge into `main` only when the user asks** ("faz o merge"), with `git merge --no-ff`, then push.
5. Update `CHANGELOG.md` (section *Unreleased*) and, when an idea is done or added, `docs/ROADMAP.md`.

The skill **`wsm-update-pi`** has the details: what needs a restart, test setup, how to read a slow restart.

## Code map
| File | Role |
|---|---|
| `wsm_service.py` | main loop: schedule/rotation, capture, `process()` of each slot, DecodePool (DSP + decoder in a separate process) |
| `wsm_capture.py` | RSP2 through SoapySDR; `SimRadio` for tests |
| `wsm_dsp.py` | decimation to 375 S/s, noise in the WSPR window, `find_spurs` (steady narrow carriers) |
| `wsm_waterfall.py` | waterfall PNG of a slot (numpy + zlib only) |
| `wsm_db.py` | SQLite schema and queries (slots, spots, syshist, events, spurs, ...); old databases get new columns with `ALTER TABLE` at start |
| `wsm_web.py` | HTTP server and the whole page (`PAGE`: HTML + CSS + JS in one string); `PUBLIC_PATHS` lists what visitors without the key may read |
| `wsm_sys.py` | Pi health: temperature, clock, `vcgencmd get_throttled` |
| `wsm_peers.py`, `wsm_missed.py`, `wsm_openings.py` | comparisons with other receivers through wspr.live |
| `tools/` | one-off diagnostics run by hand on the Pi (`wsm_diag.py`, `wsm_antenna_compare.py`) |

## Rules of the code
- **Runs on a Pi 3B+**: numpy and scipy only, no plotting or heavy libraries; keep per-slot work small (a few ms) and
  never hold the GIL for long in the main process (the capture thread loses samples).
- Nothing in the per-slot path may fail the slot: helpers like `window_noise`/`window_spurs` catch everything and return None.
- Health and diagnostics are **key only**: new APIs stay out of `PUBLIC_PATHS`, and station-health fields are stripped
  from public answers.
- Content-Security-Policy allows no inline script except the page's own `<script>` blocks, by SHA-256 (`script_hashes`):
  never use `onclick=` / `on*=` attributes, `javascript:` links, `eval` or `new Function`; attach handlers in the script.
- Every card, panel and table on the page has a `?` help text (the `HELP` object in the page script of `wsm_web.py`); extend it when adding one.
- Database changes must work on an existing database (check `PRAGMA table_info`, add the column).
- New behaviour gets a check in `wsm_selftest.py`.
- Times: the database is UTC; the page shows local time where it says so.

## Testing here (cloud container)
The Pi has everything installed; a fresh container does not:
```bash
pip install numpy scipy pyyaml && sudo apt-get install -y libfftw3-dev && make -C decoder
python3 wsm_selftest.py
```
`db1.wspr.live` is usually not reachable from the container: test wspr.live code with mocked answers.
