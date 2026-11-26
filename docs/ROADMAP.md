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
