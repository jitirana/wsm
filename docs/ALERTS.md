# Alerts: what they mean and what to do

WSM sends its alerts as ntfy notifications titled `WSM: ...`. Every alert has three moments: the **onset**, a **reminder**
every `health.repeat_h` hours while it lasts (`... (still active)`), and **`recovered: ...`** when it clears.
Thresholds are in the `health:` section of `wsm.yaml` ([CONFIGURATION.md](CONFIGURATION.md)).

Useful everywhere:

```bash
journalctl -u wsm --since "-1h" --no-pager | tail -40     # what WSM did
python3 ~/wsm/wsm_health.py --status                      # active alerts and recent self-heal restarts
vcgencmd get_throttled; vcgencmd measure_temp; vcgencmd measure_clock arm   # Raspberry Pi power and temperature
```

The **System** card on the web page shows the same health data (hover over it for details).

---

## WSM: under-voltage (power supply)

**Meaning.** In the last hour the Raspberry Pi reported under-voltage at least `power_events_max` times (default 10), or for 5 %
of the time. During each event the firmware holds the CPU at 600 MHz; the SDRplay API then cannot keep up and **silently drops
samples**, so slots come out incomplete and decoding fails. Repeated under-voltage can also corrupt the SD card.

**What to do.**
1. Look at the timing: `dmesg -T | grep -i voltage | tail`. Events only at boot or when a USB device is plugged in are normal;
   events every few minutes are not.
2. Use a proper supply: the official Raspberry Pi supply (5.1 V), with its own short, thick cable. Phone chargers and thin or long
   cables are the usual culprits.
3. Reduce the load on the Pi's USB ports: an SDR dongle draws 100–300 mA even when idle. A **powered USB hub** for the radios
   takes that current off the Pi.
4. If a good supply still gives under-voltage after hours of use, the Pi's own power input (polyfuse / connector) may be worn.
   This station cured exactly that by moving the SD card from a Pi 3B to a Pi 3B+ with the same supply.

## WSM: computer is too hot

**Meaning.** CPU temperature above `temp_max_c` (default 75 °C). The alert clears 3 °C below the limit. Around 80 °C the Pi
slows the CPU down, and a Pi 3B+ already drops from 1.4 to 1.2 GHz at 60 °C.

**What to do.** Heatsinks need moving air: open or ventilate the case, add a 5 V fan (pins 4/6, or 1/6 for a quieter 3.3 V), or
use an aluminium case that works as a heatsink. Keep the Pi out of the sun. Air conditioning cools the room but does not move air
over the board.

## WSM: samples are being lost

**Meaning.** The average slot coverage in the last hour is below `coverage_min` (default 95 %): parts of the 2-minute recordings
are missing. The log shows `slot covered NN %` and often `arrival-time rate ... differs`.

**What to do.** This is almost always the computer, not the radio:
1. **Power** – see "under-voltage" above (the most common cause).
2. **Temperature / CPU clock** – see "too hot"; check `vcgencmd measure_clock arm`.
3. **CPU priority** – the `sdrplay_apiService` process must have a higher priority than other busy programs
   (`ps -eo ni,pcpu,comm --sort=-pcpu | head`); see INSTALL.md, "Priority of the SDRplay API".
4. **USB** – a loose cable or a busy USB bus; try another port.

## WSM: radio stalled

**Meaning.** No good slot for `stall_min` minutes (default 10), or the radio thread hangs (stuck opening the RSP2, or a slot long
overdue). With `self_heal: yes` WSM restarts itself; the second time it also restarts the SDRplay API service. You then get
**`WSM: restarted itself`** with the reason.

**What to do.** Usually nothing: watch for `recovered: radio stalled`. If it keeps coming back, check the RSP2 cable and USB port,
`lsusb` (the RSP2 is `1df7:3010`) and `journalctl -u sdrplay -n 30`.

## WSM: radio problem, manual action needed

**Meaning.** Self-healing was tried `max_restarts` times within `window_min` minutes and the radio still does not work, or
self-heal is off.

**What to do.**
```bash
sudo systemctl stop wsm && sudo systemctl restart sdrplay && sleep 3 && sudo systemctl start wsm
```
If that does not help: unplug and re-plug the RSP2 (or power-cycle the Pi), check `lsusb`, the power supply and
`journalctl -u sdrplay`.

## WSM: restarted itself

Information only: the self-heal restarted WSM (the message says why and whether the SDRplay service was restarted too).
Frequent restarts point to a USB, power or radio problem.

## WSM: no spots

Only when `no_spots_hours` > 0. No station was decoded for that many hours while the radio works. Check the antenna and its
connection, the band rotation (are the bands open at this time of day?) and the input level on the web page (a peak near 0 %
means no signal reaches the receiver; near 100 % means overload).

## WSM: low disk space

Less than `disk_min_mb` free in the data directory. Check `df -h /` and `du -sh ~/wsm/data/*`; clean old backups or logs, or
lower `backup.keep`.

## WSM: database is big

`wsm.sqlite` is larger than `db_max_mb`. Set `keep_days` (for example 365) to drop old spots, or raise the limit.

## WSM: system clock is off

The system time differs from internet time by more than `clock_max_s`, or NTP is not synchronised. WSPR needs the clock within
about 1 s, otherwise nothing decodes. Check `timedatectl`, `chronyc tracking` and the network.

## WSM: backup failed / backup works again

The daily backup did not complete. The message carries the error; details: `journalctl -u wsm | grep backup`. Typical causes:
disk full, permissions, or an archive above `max_archive_mb` (look for large files pulled in by `extra_paths`).
