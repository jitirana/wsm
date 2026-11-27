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
dedup_same_call: yes       # same callsign + locator decoded several times in one slot (copies of a very strong signal): keep only the best SNR
debug_keep_iq: 0            # N > 0: keep the decoder input of the last N slots in data/debug/ (~360 kB each, for diagnosis)
waterfall: yes              # keep a waterfall picture of the last slot of each band (RAM only, ~130 kB per band)
decode_process: yes         # decode in a separate low-priority process (recommended on a Raspberry Pi)
allow_with_sbm: no          # WSM refuses to start while sbm.service (another program using the RSP2) runs

decoder:
  engine: wsjtx             # wsjtx: the WSJT-X wsprd (about 5 dB more sensitive); legacy: wsm_decode (rtlsdr-wsprd, the fallback)
  path: ~/wsm/decoder/wsm_decode          # the legacy decoder
  wsjtx_path: ""            # default: wsprd_wsjtx in the same directory as `path`
  flags: []                 # extra options for the WSJT-X wsprd, e.g. ["-d"] (deeper search, a little slower)
  hashtable: no             # yes: let the decoder resolve hashed callsigns (type 3 messages) across slots
```

## Web page

```yaml
web:
  enabled: yes
  host: 127.0.0.1           # keep it local and publish through a tunnel
  port: 8091
  token: ""                 # if set, log in at http://host:port/login (a cookie remembers it); use 16+ characters
  public: no                # yes: visitors without the key get a read-only public view (needs a token)
  url_key: no               # yes: also accept old /?key=TOKEN links (the key then lands in history and proxy logs)
```

**Logging in and out.** `/login` asks for the key once and sets a session cookie (HttpOnly, SameSite=Strict, 30 days).
The header of the page has **log out** (this browser) and **log out everywhere** (every browser and phone: the server
makes a new session, so all the old cookies stop working). Changing `token` and restarting also ends every session.
The session is kept in `data/web_session` (mode 600). Scripts send the key in the `X-WSM-Key` header instead: they get the
answer directly, without a cookie.

The `/about` page ("What is WSPR?") is always public. Without `public`, everything else needs the token when one is set.
With `public: yes` a visitor without the key sees the cards, waterfall, band table, map and spot list (at most 1000 spots);
the **System** card, `/api/system` and the CSV export stay behind the key, and the station position is given only as the
centre of its 6-character locator square (the same locator WSPRnet shows).

## Station landing page

A public page for the whole station, served when the request comes for one of `hosts` (publish that name with the tunnel,
see INSTALL.md). Everything in it is public: do not put addresses, exact coordinates or private feeder keys here.

```yaml
landing:
  enabled: no
  hosts: [example.org]      # host names that get the landing page at "/"
  title: Amateur radio station
  about: "Receiving station for WSPR and ADS-B in ..."
  equipment: ["Raspberry Pi 3B+", "SDRplay RSP2 (WSPR)", "RTL-SDR dongle (ADS-B 1090 MHz)"]
  links:                    # shown under "About the station"
    - {name: QRZ.com, url: "https://www.qrz.com/db/CALL"}
  projects:                 # "Other projects": name, short description, optional link
    - {name: "SSM - SID System Monitor", text: "...", url: "https://...", link: "Open SSM"}
  monitor_url: https://wsm.example.org/     # the WSPR monitor (its /about is linked as "What is WSPR?")
  adsb_url: https://fsm.example.org/        # live aircraft map (tar1090)

adsb:
  json_dir: /run/readsb     # where readsb writes aircraft.json, receiver.json, stats.json
```

The page refreshes every 30 s from `/api/landing` (cached for 30 s on the server). The ADS-B numbers (aircraft now, messages
per second, farthest aircraft) need readsb on the same computer; without it that section shows "receiver offline".

**While WSM is switched off** (for example only ADS-B running), lighttpd can serve the same page: run
`python3 tools/wsm_landing_snapshot.py -c ~/wsm/wsm.yaml` (writes `station.html`, `favicon.svg` and `landing.json` into
`/var/www/station`; create the folder once with `sudo mkdir -p /var/www/station && sudo chown pi: /var/www/station`),
enable `deploy/lighttpd-station.conf` for the landing host name and point the tunnel for that host name at lighttpd
(port 80). The page then says that the WSPR monitor is temporarily unavailable, keeps the texts and the best DX ever, and
shows the ADS-B numbers live from `/tar1090/data/aircraft.json`. When WSM runs again, point the tunnel back at WSM: the
page uses `/api/landing` again by itself.

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

## Band openings
The "Band openings" panel shows, for each band and hour of the day (UTC), the spots per listened hour of this receiver and a
reference from the public WSPRnet database (https://wspr.live) for stations in your region. All keys are optional.

```yaml
openings:
  enabled: yes
  days: 30              # default period of the panel (the page can change it)
  reference:
    enabled: yes        # no = never contact wspr.live
    days: 14            # period of the reference
    min_km: 500         # only spots at least this long (skips local ground-wave)
    radius_deg: 6.0     # region around the station in degrees (6 = about 650 km); rounded to whole degrees
    direction: both     # rx = region stations as receivers, tx = as transmitters, both = either
    refresh_hours: 24
    bands: []           # default: the bands of the radios
```
The query is read-only, one request per band spaced by a few seconds, once a day; your own callsign is excluded.
Test: `python3 wsm_openings.py -c ~/wsm/wsm.yaml --fetch`, then `--show` prints the stored matrix.

## Alternating antennas (`band@antenna`)
A rotation or schedule entry can name the antenna input for that slot: `20m@A` or `20m@HIZ` (A, B or HIZ). Alternating the two on
one band gives the fairest antenna comparison, because both listen to the same band a few minutes apart:

```yaml
radios:
  - name: rsp2
    schedule:
      "09-21": [20m@A, 20m@HIZ]       # 20 m, antenna A and HIZ in turn, one slot each
      "21-09": [40m@A, 40m@HIZ]
```
An entry without `@` uses the antenna of the radio or of the band, as before. The antenna of each slot is stored in the database, so
`tools/wsm_antenna_compare.py` can show, besides the usual table, the SNR difference for the same station heard in two adjacent slots
on different antennas. Keep the same `gain_db` for both and check that the input level stays below about 50 %.

## Peer comparison
Compares this receiver with the others that heard the same station in the same slot (public WSPRnet database, wspr.live), so
that antennas, gains or a new location can be compared without depending on propagation. For each spot, the receivers at a similar
distance from that transmitter are the peers; `delta = my SNR - median SNR of the peers` (+ = better than them). All keys optional.

```yaml
peers:
  enabled: yes              # needs openings.reference.enabled (same wspr.live access)
  window_pct: 10            # peers: distance to the transmitter within this % of mine ...
  window_min_km: 700        # ... or this many km, whichever is larger
  min_peers: 3              # fewer peers: no delta for that spot
  delay_min: 30             # look a spot up when it is this old
  per_cycle: 40             # spots per round, one round every cycle_min minutes
  cycle_min: 20
  days: 3                   # ignore spots older than this
```
Read-only requests, one per spot, spaced to stay under 20 per minute; only the heard callsign, band and slot time are sent.
`python3 wsm_peers.py -c ~/wsm/wsm.yaml --run` looks up pending spots now, `--show [--since "YYYY-MM-DD HH:MM"]` prints the
summary by band and antenna, and `tools/wsm_antenna_compare.py` shows it under its own table.

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
