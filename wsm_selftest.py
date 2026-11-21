#!/usr/bin/env python3
"""End-to-end test with simulated radios: schedule-free (synthetic slot times), real DSP, real decoder, SQLite, web page."""
import json, os, sys, tempfile, time, urllib.request, queue
import wsm_config, wsm_db, wsm_service, wsm_web, wsm_capture

here = os.path.dirname(os.path.abspath(__file__))
tmp = tempfile.mkdtemp(prefix="wsm_selftest_")
cfg = wsm_config.load()
cfg["data_dir"] = tmp
cfg["store_own_spots"] = True                       # the test decodes PU2TKN = the default station callsign
cfg["decoder"]["path"] = os.path.join(here, "decoder", "wsm_decode")
cfg["web"].update(port=int(os.environ.get("WSM_TEST_PORT", 8191)))
r = cfg["radios"][0]
r["name"] = "sim"
r["sim_encoder"] = os.path.join(here, "decoder", "wsm_symbols")
r["rotation"] = ["20m", "40m", "30m", "10m"]
r["sim"] = {"20m": [dict(msg="PU2TKN HI03 0", snr=-5, offset=-1.4, dt=1.0), dict(msg="K1ABC FN42 37", snr=-18, offset=20, dt=0.4, drift=0.5)],
            "40m": [dict(msg="PY2XYZ GG66 30", snr=-12, offset=-30, dt=1.2), dict(msg="DL1ABC JO62 23", snr=-22, offset=45, dt=0.8)],
            "30m": [], "10m": [dict(msg="VK2ABC QF56 20", snr=-20, offset=10, dt=1.0)]}
db = wsm_db.DB(os.path.join(tmp, "wsm.sqlite"), cfg["station"]["grid"])
radio = wsm_capture.SimRadio(r, lambda m: None)
base = (int(time.time()) // 120 - 12) * 120
tot = 0
for k in range(8):                                  # two full rotations, 8 slots
    band = r["rotation"][k % 4]
    slot = base + k * 120
    rec = radio.record(band, wsm_config.BANDS[band], slot, 116)
    n, note = wsm_service.process(cfg, db, dict(rcfg=r, band=band, dial=wsm_config.BANDS[band], slot=slot, rec=rec))
    print(band, time.strftime("%H:%M", time.gmtime(slot)), n, "spot(s)", note)
    tot += n
print("total", tot)
for s in db.spots(0):
    print(s["band"], f"{s['freq_hz']/1e6:.6f}", s["snr"], s["dt"], s["call"], s["grid"], s["power_dbm"], round(s["dist_km"] or 0), s["map"])
state = wsm_web.State(cfg, ["20m", "40m", "30m", "10m"])
state.set("sim", state="listening", band="20m", next_ts=time.time() + 60)
srv = wsm_web.start(cfg, db, state)
u = f"http://127.0.0.1:{cfg['web']['port']}/"
for p in ["", "api/status", "api/summary?hours=720", "api/spots?since=0", "api/slots", "spots.csv?since=0"]:
    d = urllib.request.urlopen(u + p).read()
    print(p or "/", len(d), "bytes")
print(json.loads(urllib.request.urlopen(u + "api/summary?hours=720").read())["bands"])
print("TMP", tmp, "PORT", cfg["web"]["port"])
if os.environ.get("WSM_KEEP"):
    time.sleep(int(os.environ["WSM_KEEP"]))
