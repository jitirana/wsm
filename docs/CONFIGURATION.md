# Configuration reference (`wsm.yaml`)

WSM reads `~/wsm/wsm.yaml`. Every key is optional: what you leave out keeps the default shown here. Start from
`wsm.yaml.example`. After a change: `sudo systemctl restart wsm`, then check the log (`journalctl -u wsm -n 20`): a wrong
value stops the service with a message that names the problem.

`wsm.yaml` contains secrets (web token, ntfy topic, heartbeat URL). Keep it out of git (it is in `.gitignore`) and never paste
it in public.

## Station and storage

```yaml
station:
  callsign: PU2TKN          # your callsign (reporter on WSPRnet)
  grid: HI03                # 4-character Maidenhead locator
  lat: -6.5                 # optional, decimal degrees: gives the 6-character locator sent to WSPRnet
  lon: -39.0                #   and exact distances; without it the centre of the grid square is used

data_dir: ~/wsm/data        # SQLite database (wsm.sqlite), state files, backups
keep_days: 0                # delete spots older than N days; 0 = keep everything
store_own_spots: no         # no: spots of your own callsign (TX = RX) are not stored; their SNR/DT still go to the log
debug_keep_iq: 0            # N > 0: keep the decoder input of the last N slots in data/debug/ (~360 kB each, for diagnosis)
waterfall: yes              # keep a waterfall picture of the last slot of each band (RAM only, ~130 kB per band)
decode_process: yes         # decode in a separate low-priority process (recommended on a Raspberry Pi)
allow_with_sbm: no          # WSM refuses to start while sbm.service (another program using the RSP2) runs

decoder:
  path: ~/wsm/decoder/wsm_decode
  hashtable: no             # yes: let the decoder resolve hashed callsigns (type 3 messages) across slots
```

## Web page

```yaml
web:
  enabled: yes
  host: 127.0.0.1           # keep it local and publish through a tunnel
  port: 8091
  token: ""                 # if set, open http://host:port/?key=TOKEN once (a cookie remembers it); use 16+ characters
```

The `/about` page ("What is WSPR?") is always public; everything else needs the token when one is set.

## Radio

`radios` is a list; WSM is tested with one SDRplay RSP2.

```yaml
radios:
  - name: rsp2
    driver: sdrplay           # sdrplay (SoapySDRPlay3) or sim (simulated signals, for tests)
    sample_rate: 62500        # S/s asked from the driver (any value >= 31250 works)
    if_bandwidth: 200000      # RSP2 IF filter, Hz
    offset_hz: 10000          # tune this far above dial+1500 Hz and shift back in software (keeps the DC spike away)
    antenna: A                # A, B (50 ohm) or HIZ (high impedance long-wire input, 1 kHz-30 MHz)
    gain_db: 30               # AGC is off; keep "Input level (peak)" below ~50 %, never above 90 %
    ppm: 0.0                  # frequency correction of the RSP2 reference
    clock_offset_s: 0.0       # added to the slot timestamps; see INSTALL.md, "Calibrate the time offset"
    rotation: [20m, 40m, 30m, 10m]      # one 2-minute slot per entry; repeat a band to give it more time
    schedule:                 # optional rotation by time of day, UTC ranges (HH or HH:MM); "21-09" wraps over midnight
      "09-21": [20m, 20m, 10m, 20m]     # day
      "21-09": [40m, 30m, 20m, 40m]     # night (outside every range the plain rotation is used)
    bands:                    # per-band overrides of any key above
      40m: {gain_db: 25}
      80m: {gain_db: 20, antenna: HIZ}
```

Known bands: 160m, 80m, 60m, 40m, 30m, 20m, 17m, 15m, 12m, 10m, 6m, 4m, 2m, 70cm (WSPR dial frequencies built in).

## Health monitor and alerts

Alerts are sent with [ntfy](https://ntfy.sh): install the ntfy app on your phone and subscribe to a topic with a long, random
name. What each alert means and what to do: [ALERTS.md](ALERTS.md). Test: `python3 wsm_health.py --test`.

```yaml
health:
  enabled: yes
  ntfy_topic: ""            # default: daily_report.ntfy_topic. Accepts NAME, ntfy.sh/NAME or https://server/NAME
  ntfy_server: https://ntfy.sh
  self_heal: yes            # restart WSM (and the SDRplay service) automatically when the radio hangs
  check_s: 15               # seconds between checks
  stall_min: 10             # no good slot for this long = radio stalled
  opening_s: 90             # stuck opening the RSP2 for this long = hang
  hang_s: 300               # a slot overdue by this long = hang
  max_restarts: 2           # self-heal restarts allowed per window_min, then "manual action needed"
  window_min: 60
  repeat_h: 6               # reminder interval of an alert that is still active
  no_spots_hours: 0         # alert when no station was decoded for N hours (0 = off)
  disk_min_mb: 500
  db_max_mb: 1000
  clock_max_s: 2.0
  power_events_max: 10      # under-voltage events per hour (or 5 % of the time) -> "under-voltage" (Raspberry Pi only)
  temp_max_c: 75            # CPU temperature alert (cleared 3 C below)
  coverage_min: 95          # average slot coverage (%) in the last hour below this -> "samples are being lost"
  heartbeat_url: ""         # optional: pinged every heartbeat_min while the radio works (url + "/fail" when it does not)
  heartbeat_min: 5
```

The self-heal step that restarts the SDRplay service needs the `ExecStartPre` line of `deploy/wsm-pi.service` (installed by
`install_pi.sh`).

## WSPRnet upload

```yaml
wsprnet:
  upload: no                # the master switch
  rcall: ""                 # reporter callsign, default station.callsign
  rgrid: ""                 # reporter locator, default: 6 characters from station.lat/lon, else station.grid
  upload_own_tx: no         # also report your own transmitter (normally pointless)
  max_age_h: 12             # never upload spots older than this (after an outage)
  url: http://wsprnet.org/post
```

When the upload is switched on, only spots decoded from then on are sent. Status: `python3 wsm_wsprnet.py --status`.

## Daily report

One ntfy message a day with the farthest station heard since the previous one, the place shown as city and country.

```yaml
daily_report:
  enabled: yes
  time: "17:00:00"          # local time
  timezone: America/Fortaleza
  ntfy_topic: ""
  ntfy_server: https://ntfy.sh
  exclude_calls: []         # callsigns to ignore
  send_when_empty: yes      # send "nothing heard" when no station was heard
  geocoder: nominatim       # nominatim (OpenStreetMap) or none
  language: en              # language of the place names
  grace_min: 30             # if WSM was down at that time, still send within this many minutes
```

Test: `python3 wsm_report.py --demo` (sample) or `--dry-run` (real report, printed only).

## Backups

```yaml
backup:
  enabled: yes
  time: "03:30"             # local time of the daily backup
  timezone: America/Fortaleza
  keep: 14                  # newest N archives are kept
  dir: ""                   # default: <data_dir>/backups
  extra_paths: []           # more directories to include
  extra_files: []           # more single files
  exclude_dirs: []          # directories to leave out (full paths or plain names)
  exclude: ["*.wav", "*.iq", "*.log", "*.sqlite", ...]   # file patterns left out (the database goes in as a safe snapshot)
  max_file_mb: 5            # files bigger than this are left out (listed in MANIFEST.txt)
  max_archive_mb: 500       # an archive bigger than this is discarded and reported
  encrypt_passphrase_file: ""   # file (chmod 600) with a passphrase -> AES-256 via openssl; empty = no encryption
  notify: yes               # ntfy when a backup fails (and when it works again)
```

Commands: `python3 wsm_backup.py --now | --list | --verify FILE | --restore FILE --to DIR`. An archive contains `wsm.yaml`,
so it contains your secrets: encrypt it before copying it to a shared place.
