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
cfg["decoder"]["wsjtx_path"] = os.path.join(here, "decoder", "wsprd_wsjtx")
cfg["decoder"]["engine"] = os.environ.get("WSM_TEST_ENGINE", "wsjtx")      # wsjtx (default) or legacy
print("decoder engine:", wsm_service.decoder_engine(cfg))
_t = wsm_service.parse_wsprd_output("2611 -15  0.3  14.097120  0  PU2TKN HI03 10\n2611 -24  1.2  14.097050 -1  PJ4/K1ABC 37\n"
                                    "2611 -27 -0.4  14.097200  0  <PU2TKN> HI03mi 33\n<DecodeFinished>\n")
assert [x["call"] for x in _t] == ["PU2TKN", "PJ4/K1ABC", "<PU2TKN>"] and _t[1]["loc"] == "" and _t[2]["dt"] == -0.4, _t
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
import numpy as np, sqlite3, wsm_dsp                  # noise in the WSPR window: right level, not moved by signals or a gap; old databases get the column
_r = np.random.default_rng(3); _n = (_r.normal(size=45000) + 1j * _r.normal(size=45000)) * 2e-3 / np.sqrt(2)
_truth = 10 * np.log10((2e-3) ** 2 * 2500 / 375)
_tt = np.arange(45000) / 375.0
assert abs(wsm_dsp.noise_floor_db(_n) - _truth) < 0.4, (wsm_dsp.noise_floor_db(_n), _truth)
assert abs(wsm_dsp.noise_floor_db(_n + sum(1e-3 * np.exp(2j * np.pi * f * _tt) for f in (-60.3, -20.1, 15.2, 41.7))) - _truth) < 0.5
_g = _n.copy(); _g[10000:22000] = 0
assert abs(wsm_dsp.noise_floor_db(_g) - _truth) < 0.5 and wsm_dsp.noise_floor_db(np.zeros(45000, np.complex64)) is None
_ns = [r["noise_db"] for r in db.history(0)["slots"]]
assert _ns and all(v is not None for v in _ns), _ns
_old = os.path.join(tmp, "old.sqlite"); _c = sqlite3.connect(_old)
_c.execute("CREATE TABLE slots (id INTEGER PRIMARY KEY, ts INTEGER NOT NULL, band TEXT NOT NULL, dial_hz INTEGER NOT NULL, radio TEXT, antenna TEXT, gain_db REAL, n_spots INTEGER NOT NULL DEFAULT 0, rate_hz REAL, peak REAL, rms_db REAL, status TEXT NOT NULL DEFAULT 'ok', note TEXT, UNIQUE (ts, band))")
_c.execute("INSERT INTO slots (ts,band,dial_hz,rms_db) VALUES (1,'20m',14095600,-30)"); _c.commit(); _c.close()
_d = wsm_db.DB(_old, "HI03"); assert _d.history(0)["slots"][0]["noise_db"] is None
_d.add_slot(3, "20m", 14095600, "rsp2", "HIZ", 30, 62500, 0.1, -30, "ok", "", -57.5); assert _d.history(0)["slots"][1]["noise_db"] == -57.5
import time as _tm, wsm_web                      # Health history "1 h": only the last hour, one point per slot, and the button exists
_h = wsm_db.DB(os.path.join(tmp, "h1.sqlite"), "HI03"); _now = int(_tm.time()) // 120 * 120
for _i in range(40):                                  # 80 minutes of 2-minute slots, one band
    _h.add_slot(_now - _i * 120, "20m", 14095600, "rsp2", "HIZ", 30, 62500, 0.1, -30, "ok", "", -50.0)
_r1 = _h.history(_now - 3600, 300)
assert _r1["bucket"] == 300 and 29 <= len(_r1["slots"]) <= 31 and min(x["ts"] for x in _r1["slots"]) >= _now - 3600, len(_r1["slots"])
assert 'data-h="1">1 h</button>' in wsm_web.PAGE and "span<=2*3600?600" in wsm_web.PAGE
print("noise in the WSPR window: ok", [round(v) for v in _ns][:4])
import wsm_wsprnet                                  # the saved upload state must follow the database (ids restart from 1 in a new one)
_st = wsm_wsprnet.settings(cfg); _top = wsm_wsprnet._max_id(db); _lg = []
wsm_wsprnet._save(cfg, {}); assert wsm_wsprnet.initial_state(cfg, db, _st, _lg.append)["last_id"] == _top
wsm_wsprnet._save(cfg, {"last_id": _top + 95}); assert wsm_wsprnet.initial_state(cfg, db, _st, _lg.append)["last_id"] == 0 and "recreated" in _lg[-1]
assert wsm_wsprnet._load(cfg)["last_id"] == 0
wsm_wsprnet._save(cfg, {"last_id": max(_top - 1, 0)}); assert wsm_wsprnet.initial_state(cfg, db, _st, _lg.append)["last_id"] == max(_top - 1, 0)
wsm_wsprnet._save(cfg, {}); print("wsprnet state: ok")
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
