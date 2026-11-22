# WSM – WSPR System Monitor

WSM turns an **SDRplay RSP2** and a **Raspberry Pi** into a 24/7 WSPR receiving station. It listens to the WSPR bands in turn,
decodes every 2-minute slot with the WSJT-X decoder, stores the spots in SQLite, reports them to **WSPRnet** and shows them on a
live web page with a map. It also watches its own health (power, temperature, lost samples, radio hangs) and sends alerts to
your phone through **ntfy**.

Station PU2TKN, grid HI03 (north-east Brazil).

## Features

- **Band scheduler** – one band per 2-minute slot, a plain rotation or a rotation by time of day (UTC), e.g. 20 m by day and
  40/30 m at night.
- **Decoder** – the WSPR decoder of WSJT-X / rtlsdr-wsprd (GPL-3.0), fed with 375 S/s complex baseband aligned to the even minute.
- **Raspberry Pi friendly** – DSP in blocks (about 85 MB per slot instead of 370 MB), decoding in a separate low-priority process,
  scratch files in RAM (`/dev/shm`) so the SD card is spared.
- **Web page** – receiver state, input level, system health, spots, stations, best DX (period and all-time), band table,
  Leaflet map with great-circle lines and the day/night shade, spot list with filters and CSV export, light/dark theme, a "?" explanation on
  every card and column. Read-only, protected by a key.
- **Waterfall** – a picture of the last 2-minute slot of each band (frequency across, time downwards) with the decoded
  callsigns drawn on their traces, so you can see what the decoder saw, including signals too weak to decode.
- **"What is WSPR?"** – a public, bilingual (English / Portuguese) page at `/about` that explains the mode to visitors.
- **WSPRnet upload** – the same HTTP report WSJT-X uses; your own transmitter is never reported.
- **Health monitor and self-healing** – radio stall/hang detection with automatic restarts, plus alerts for under-voltage,
  temperature, sample loss, disk, database size and clock. See [docs/ALERTS.md](docs/ALERTS.md).
- **Daily report** – one ntfy message a day with the farthest station heard.
- **Backups** – one archive a day (database snapshot, code, configuration, system files) with rotation.

## Quick start (Raspberry Pi)

```bash
git clone https://github.com/jitirana/wsm.git ~/wsm
cd ~/wsm
cp wsm.yaml.example wsm.yaml      # then edit: station callsign, grid, lat/lon, web token ...
./install_pi.sh --start
```

The installer adds the packages, builds the SoapySDRPlay3 module and the decoder, runs a self-test with simulated signals and
installs the `wsm` systemd service. The SDRplay API must be installed first. Details: [docs/INSTALL.md](docs/INSTALL.md).

The web page listens on `http://127.0.0.1:8091/` (local only); publish it with a tunnel such as cloudflared.

## Documentation

| Document | Contents |
|---|---|
| [docs/INSTALL.md](docs/INSTALL.md) | installation and update on a Raspberry Pi, first checks |
| [docs/CONFIGURATION.md](docs/CONFIGURATION.md) | every `wsm.yaml` option |
| [docs/ALERTS.md](docs/ALERTS.md) | each alert: what it means and what to do |
| [docs/HARDWARE.md](docs/HARDWARE.md) | Raspberry Pi model, power supply, cooling, the long-wire antenna on the Hi-Z input |
| [CHANGELOG.md](CHANGELOG.md) | version history |

## Files

| File | Purpose |
|---|---|
| `wsm_service.py` | main program: band scheduler, capture, decode, database, web, health |
| `wsm_capture.py` | radio drivers: `RspRadio` (SoapySDR / SDRplay) and `SimRadio` (simulation for tests) |
| `wsm_dsp.py` | stream → 375 S/s complex baseband on the UTC grid, block by block |
| `wsm_waterfall.py` | waterfall picture (PNG, numpy only) of the last slot of each band |
| `decoder/` | `wsm_decode` and `wsm_symbols` (C, GPL-3.0, from rtlsdr-wsprd / WSJT-X) |
| `wsm_db.py`, `wsm_geo.py` | SQLite storage; locator → position, distance, bearing |
| `wsm_web.py`, `static/` | web page, JSON/CSV API, the `/about` page |
| `wsm_sys.py`, `wsm_health.py` | computer health sampling; alerts and self-healing |
| `wsm_wsprnet.py`, `wsm_report.py`, `wsm_backup.py` | WSPRnet upload, daily report, backups |
| `wsm_selftest.py`, `wsm_testsignal.py` | end-to-end test with simulated WSPR signals (no radio needed) |
| `install_pi.sh`, `deploy/wsm-pi.service` | installer and systemd unit template |
| `tools/` | RSP2 probes used during development (sample rate, tuning) |

## API

`/api/status`, `/api/summary?hours=24[&noown=1]`, `/api/spots?since=EPOCH&band=20m&call=PY*&limit=500[&noown=1]`,
`/api/slots`, `/api/system`, `/api/waterfall` (last slot per band, decodes with their offset in Hz),
`/api/waterfall.png?band=20m`, `/spots.csv`. All need the web key except `/about`.

## Credits and license

The WSPR protocol and decoder are by Joe Taylor K1JT, Steven Franke K9AN and the WSJT-X team; the decoder front end comes from
Guenael's rtlsdr-wsprd. Leaflet and OpenStreetMap for the map.

WSM is free software under the **GNU General Public License v3.0** – see [LICENSE](LICENSE).
