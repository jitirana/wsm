# Installing WSM on a Raspberry Pi

Tested on a **Raspberry Pi 3B+** with Raspberry Pi OS (Debian 12, "Bookworm") and an **SDRplay RSP2**. A Pi 4 or 5 works too.
Read [HARDWARE.md](HARDWARE.md) first: an unstable power supply was the main source of trouble while building this station.

## 1. Before you start

- Raspberry Pi OS installed, network working, SSH access as the normal user (here `pi`).
- The clock must be right: WSPR needs the system time within about 1 second. Raspberry Pi OS uses `chrony` or
  `systemd-timesyncd`; check with `timedatectl` (`System clock synchronized: yes`).
- The desktop is not needed. Booting to the console saves memory and CPU:
  `sudo raspi-config nonint do_boot_behaviour B1` and reboot.
- **SDRplay API**: download the Linux ARM installer (API 3.x) from sdrplay.com and run it:

  ```bash
  sudo sh SDRplay_RSP_API-*.run
  ```

  It installs the `sdrplay` systemd service (`systemctl status sdrplay`).

## 2. Get the code and configure

```bash
git clone https://github.com/jitirana/wsm.git ~/wsm
cd ~/wsm
cp wsm.yaml.example wsm.yaml
nano wsm.yaml
```

At least set `station.callsign`, `station.grid` (4 characters), `station.lat` / `station.lon` (for the 6-character locator sent
to WSPRnet) and `web.token`. Everything else has working defaults. Every option is described in
[CONFIGURATION.md](CONFIGURATION.md). `wsm.yaml` holds secrets (web token, ntfy topic): it is listed in `.gitignore` and must
never be committed.

## 3. Run the installer

```bash
./install_pi.sh --start
```

It is safe to run again. Step by step it:

1. installs the packages (`build-essential cmake git libfftw3-dev python3-numpy python3-scipy python3-yaml python3-soapysdr
   soapysdr-tools libsoapysdr-dev sqlite3`);
2. checks the SDRplay API and enables its service;
3. builds and installs the **SoapySDRPlay3** module if SoapySDR does not see the RSP2 yet;
4. builds the decoder (`decoder/wsm_decode`);
5. runs the **self-test**: simulated WSPR signals go through the real DSP, decoder, database and web page; it must decode PU2TKN;
6. installs `/etc/systemd/system/wsm.service` from `deploy/wsm-pi.service` and, with `--start`, enables and starts it.

## 4. First checks

```bash
journalctl -u wsm -f
```

One line per slot, every 2 minutes, for example:

```
[rsp2] 20m slot 12:28Z: 1 spot(s), peak 9 %, 10.2 s
```

- no `slot covered NN %` and no `overflow` → the radio stream is complete;
- the last number (decode time) should stay well below 120 s;
- `peak` should stay below about 50 % (lower `gain_db` if it gets near 90 %).

Open the web page (`http://127.0.0.1:8091/?key=YOUR_TOKEN` on the Pi, or through your tunnel). The **System** card must show
`power OK` and `coverage 100 %`.

Check the real sample rate of the RSP2 once, with WSM stopped:

```bash
sudo systemctl stop wsm && python3 tools/wsm_probe.py 62500; sudo systemctl start wsm
```

`ratio` should be about 1.000.

## 5. Recommended system settings

**Priority of the SDRplay API.** The API decimates 2 MS/s to 62.5 kS/s on the CPU and must never fall behind. If other programs
run on the Pi (for example an ADS-B decoder), give it a higher priority:

```bash
sudo mkdir -p /etc/systemd/system/sdrplay.service.d
printf '[Service]\nNice=-10\n' | sudo tee /etc/systemd/system/sdrplay.service.d/priority.conf
sudo systemctl daemon-reload && sudo systemctl restart sdrplay wsm
```

**Persistent journal** (keeps the logs across reboots, limited to 100 MB):

```bash
sudo mkdir -p /var/log/journal /etc/systemd/journald.conf.d
printf '[Journal]\nSystemMaxUse=100M\n' | sudo tee /etc/systemd/journald.conf.d/size.conf
sudo systemctl restart systemd-journald
```

**Start after the clock is synchronised.** `deploy/wsm-pi.service` already waits for `time-sync.target`. With chrony, enable
the service that provides it: `sudo systemctl enable chrony-wait.service`.

## 6. Calibrate the time offset (optional)

With a GPS-timed transmitter on the air (one that starts at second 2 of the even minute), collect about 10 of its spots and set
in the radio section of `wsm.yaml`:

```
clock_offset_s = 1.0 − (median DT of those spots)
```

for example a median DT of 1.1 s gives `clock_offset_s: -0.1`. Spots of your own callsign are not stored
(`store_own_spots: no`), but their SNR, DT and drift appear in the log:

```bash
journalctl -u wsm --since "-1h" --no-pager | grep "own TX heard"
```

## 7. Updating

```bash
cd ~/wsm
git pull
make -C decoder
sudo systemctl restart wsm
```

`wsm.yaml` and `data/` are not tracked by git, so an update never touches your configuration or your spots.

## 8. Publishing the page

The web server listens on 127.0.0.1 only. A Cloudflare tunnel is the simplest way to publish it with HTTPS: one `cloudflared`
service can serve several hostnames, for example

```yaml
ingress:
  - hostname: wsm.example.org
    service: http://127.0.0.1:8091
  - service: http_status:404
```

Visitors need `?key=TOKEN` once (a cookie remembers it); the `/about` page is public.
With `web.public: yes` visitors without the key see a read-only public view (spots, map, waterfall; no health card, no CSV,
and the station position only to a 6-character locator square).

### Station landing page

WSM can also serve a public page for the whole station (WSPR and ADS-B numbers, equipment, links, other projects) on its own
host name, e.g. the bare domain. Fill the `landing` section of `wsm.yaml` (see CONFIGURATION.md), then send that host name
to the same port:

```yaml
ingress:
  - hostname: example.org
    service: http://127.0.0.1:8091
  - hostname: wsm.example.org
    service: http://127.0.0.1:8091
  - service: http_status:404
```

and create the DNS record once: `cloudflared tunnel route dns TUNNEL example.org`. If the name already has a DNS record (for
example GitHub Pages), remove it in the Cloudflare dashboard first.

## 9. Backups

WSM writes one archive a day to `data/backups/` (see `backup:` in [CONFIGURATION.md](CONFIGURATION.md)). Copy them off the SD
card regularly, for example by pulling them with `scp` from another computer on a schedule.
Restore: `python3 wsm_backup.py --restore ARCHIVE --to DIR`, then follow `RESTORE.md` inside the archive.
