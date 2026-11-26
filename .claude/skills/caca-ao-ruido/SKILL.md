---
name: caca-ao-ruido
description: Noise hunt at the WSM station - find which device or path raises the noise in the WSPR window, using event markers, the before/after (Δ) table, "noise vs typical hour", noise shifts, narrow spurs and tools/wsm_diag.py. Use when the user wants to test a suspect (LED, power supply, Starlink, router, the whole house on a power bank), sends a Δ or noise chart, or asks why the station hears so few spots.
---

# Noise hunt (caça ao ruído)

Reply in Brazilian Portuguese. The user does the physical steps and the clicks; you plan the test, then read the results.
What has been found so far is in `docs/ROADMAP.md`, section "Noise hunt": read it first and add every new result there.

## The tools on the page (Health history, key only)
| Tool | What it answers |
|---|---|
| **+ mark event** (text, time field empty = now) | puts a dashed line on every chart; the time field marks something done earlier |
| **Δ** beside an event | noise per band before vs after (default 30 min each side, first 10 min skipped = "Settle"); "clear change" = at least 0.5 dB and more than twice the scatter |
| **noise vs typical hour** | noise minus the median of the same band and hour over 14 days: a new local source shows as a rise |
| **Noise shifts found** | steps of 1.5 dB or more detected by themselves, with a "mark" button |
| **Narrow spurs in the WSPR window** | steady carriers per band and frequency, % of slots, SNR; the waterfall marks them "spur" |
| **CPU slowdowns per day** | rules out heat / under-voltage as the cause of short slots |
| `python3 tools/wsm_diag.py -c ~/wsm/wsm.yaml` | per band: slots, spots, median noise, peak; frequency error against WSPRnet; DT |

## One test = one change
1. Pick ONE suspect. Mark the event **before** touching it ("Starlink off"), then make the change.
2. Wait **at least 20–30 minutes** (the rotation spreads the slots over the bands; a Δ needs 2+ slots of a band on each
   side, 4+ to avoid "few slots"). To get results faster, put a single band in the rotation for the test
   (`rotation: [20m]` in `wsm.yaml`, restart) and put the normal rotation back afterwards.
3. Undo the change and mark the event ("Starlink on"). Undoing confirms the result: a real source moves the noise both ways.
4. Open the Δ of both events. If "No band has 2 or more slots" or "few slots": lower Settle to 2–4 min, or wait longer.
5. Write down band, change in dB, "clear change" or not.

Do not change two things in the same window, and do not test while the band is opening or closing (sunrise/sunset):
the natural drift then hides a 1–2 dB effect. "noise vs typical hour" helps to see the drift.

## Order of the tests
1. **Antenna off** (one slot of the band, wire out of the Hi-Z terminal): splits "comes in through the antenna" from
   "made inside the receiver / Pi / USB". With no antenna the WSPR-window noise was about -86 dB, with the wire about -44 dB.
   A spur that stays without the antenna is internal: check it by changing `offset_hz` of that band (an internal spur moves
   in RF frequency, a real signal stays put).
2. **Whole house off**: Pi and RSP2 on a power bank (5 V, 2.4–3 A at one port, short thick micro-USB cable; it must not
   be charging), main breaker off for 10–15 min. A big drop (5–20 dB) = the source is inside the house; then go circuit by
   circuit. Under-voltage on the power bank shows in the System card / CPU slowdowns table and does not spoil the noise result.
3. **Device by device**, most likely first: Starlink power supply and router, other routers and switches, LED lamps and LED
   drivers, phone / laptop chargers, TV, inverter / solar, fans with electronic speed control, the Pi's own power supply.

## Reading the result
- Same noise on 40, 30 and 20 m (about -44 dB here) is not natural: atmospheric noise on 40 m at night is 10–15 dB above
  20 m. A flat, wide noise points to a local switching source coupled into the wire or its lead-in.
- A source that is only 1 dB (the LED was +0.8 to +1.6 dB) matters less than one of 5+ dB: rank the fixes by dB.
- `wsm_peers.py --show` (my SNR minus nearby receivers) and the "Heard by others, not by me" panel measure the result of a
  fix in terms of reception, not only noise.

## Fixes, cheapest first
1. Switch off or replace the source (linear supply, better LED driver).
2. Clip-on ferrites (mix 31 for HF) on the mains and DC cables of the source, close to it.
3. Common-mode choke on the antenna lead-in; keep mains and network cables away from where the wire enters the house.
4. A counterpoise wire of a few metres on the Hi-Z GND, and a 1 MΩ resistor P–GND against static.
5. Move the feed point of the antenna away from the house (coax to the RSP2, 50 Ω port with a transformer / balun).
