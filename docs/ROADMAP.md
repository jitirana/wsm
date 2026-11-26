# Roadmap

Ideas collected on 2026-11-26 after a day of noise hunting (LED, power supplies, fan, long wire), in order of value.
Each item says where it stands. New ideas go to the end of the list.

| # | Item | State |
|---|---|---|
| 1 | **Event markers on the charts**: "mark event" (free text, time = now by default) saved in the database and drawn as a vertical line on the noise and temperature charts | done (Health history) |
| 2 | **Noise relative to the hour of the day**: median of each band per hour of the day over 7–30 days; the chart shows "above or below the normal for this hour" | done ("noise vs typical hour") |
| 3 | **Heard by others, not by me**: transmissions the neighbouring receivers decoded in my band and slot and I did not (wspr.live) | done (`wsm_missed.py`; radius 12° because nobody else is within 6°) |
| 4 | **Window comparison**: noise per band before and after a change, with the difference in dB, skipping the transition slots | done (Δ next to each event) |
| 5 | **Automatic noise steps**: "20 m noise rose 2 dB at 08:24 and stayed", with a "mark an event here" button | done ("Noise shifts found", threshold 1.5 dB) |
| 6 | **Rotation suggested by the data**: weekly report of spots per slot, band and hour against the public reference, suggesting bands to add or drop (suggests only, the operator decides) | waiting: needs 2–3 weeks of data |
| 7 | **Thermal health**: how long per day the CPU ran slowed down (frequency capped, throttled, soft temperature limit, under-voltage), and whether that coincided with short slots | done ("CPU slowdowns per day" in Health history) |
| 8 | **Narrow spur detection**: the faint vertical line in the 20 m waterfall. The `narrowband-noise` branch only added the noise measurement in the WSPR window (merged); now done by `wsm_dsp.find_spurs` | done ("Narrow spurs" in Health history, "spur" on the waterfall) |
| 9 | Noise axis labels with one decimal when the range is small | done |

## Also open
- "Heard by others": only count transmitters at a similar distance from both receivers (as `wsm_peers` does), so propagation does not explain the misses.
- Noise step threshold (1.5 dB) configurable in `wsm.yaml`.

## Noise hunt (results so far)
Procedure: skill `caca-ao-ruido`. Add each test here with the date, the band and the Δ in dB.
- 2026-11-26, 20 m: station hears about 7 dB below receivers at a similar distance (`wsm_peers`, 14 spots, Hi-Z long wire);
  2 to 10 spots per day, all on 20 m. Frequency error -0.04 ppm and DT -0.4 s: not the cause (`tools/wsm_diag.py`).
- Noise in the WSPR window about the same on 40, 30 and 20 m (-43.7 / -44.7 / -44.3 dB), 10 m -54.5 dB: a flat local source
  is suspected. Without the antenna about -86 dB.
- LED lamp: on +1.3 dB (20 m) / +0.8 dB (10 m), off -1.0 / -1.6 dB. Real, but small. Noise still 2.5–3 dB above the typical hour.
- Steady carrier at 7.040027 MHz (40 m, -22 dB, every slot, slight drift), not seen on the other bands. Next: antenna-off test.
- Next: whole house off with the Pi on a power bank; Starlink off.
