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
import time as _t2                                 # events and the typical noise of the hour (insights)
_n0 = _t2.time()
_eid = db.add_event(_n0 - 600, "  LED   on  "); assert db.events(_n0 - 3600)[-1]["text"] == "LED on" and db.events(_n0 - 3600)[-1]["id"] == _eid
_long = db.add_event(_n0, "x" * 200); assert len(db.events(_n0 - 1)[-1]["text"]) == 80; db.del_event(_long)
try:
    db.add_event(_n0, "   "); raise SystemExit("empty event accepted")
except ValueError:
    pass
assert db.del_event(_eid) == 1 and db.del_event(_eid) == 0
_bd = wsm_db.DB(os.path.join(tmp, "base.sqlite"), "HI03", (-6.0, -39.0))
_b0 = (int(_n0) // 86400 - 3) * 86400 + 5 * 3600
for _d in range(3):                                  # 3 days x 10 slots at 05 UTC: -44 dB, one odd day at -80
    for _k in range(10):
        _bd.add_slot(_b0 + _d * 86400 + _k * 120, "30m", 10138700, "rsp2", "HIZ", 30, 62500, 0.1, -30, "ok", "", -80 if _d == 1 else -44)
_bs = _bd.noise_baseline(14, now=_b0 + 4 * 86400)
assert _bs["30m"][5] == -44.0 and _bs["30m"][6] is None and set(_bs) == {"30m"}, _bs     # median ignores the odd day; other hours: no data
_bd.add_slot(_b0 + 8 * 3600, "20m", 14095600, "rsp2", "HIZ", 30, 62500, 0.1, -30, "ok", "", -44)
assert "20m" not in _bd.noise_baseline(14, now=_b0 + 4 * 86400)                      # too few slots
assert "base" in _bd.history(_b0 - 60) and "events" in _bd.history(_b0 - 60)
print("events and typical noise: ok")
import math as _m                                   # before/after of an event and level shifts found by themselves
_cd = wsm_db.DB(os.path.join(tmp, "cmp.sqlite"), "HI03", (-6.0, -39.0))
_c0 = (int(_n0) // 86400 - 1) * 86400 + 6 * 3600
_ts_step = _c0 + 5400
for _i in range(90):                                 # 3 h of 2-minute slots: 20 m steps up 2 dB at 1 h 30 min, 30 m stays flat
    _t = _c0 + _i * 120
    _cd.add_slot(_t, "20m", 14095600, "rsp2", "HIZ", 30, 62500, 0.1, -31 + (2 if _t >= _ts_step else 0), "ok", "", -44 + (2 if _t >= _ts_step else 0) + 0.3 * _m.sin(_i * 1.7))
    _cd.add_slot(_t + 1, "30m", 10138700, "rsp2", "HIZ", 30, 62500, 0.1, -31, "ok", "", -44 + 0.3 * _m.sin(_i * 2.3))
for _t in (_c0, _c0 + 120, _c0 + 240, _c0 + 360, _c0 + 480, _c0 + 600, _c0 + 3 * 3600 - 600, _c0 + 3 * 3600 - 480, _c0 + 3 * 3600 - 360, _c0 + 3 * 3600 - 240):
    _cd.add_slot(_t + 2, "10m", 28126100, "rsp2", "HIZ", 30, 62500, 0.1, -40, "ok", "", -55 if _t < _c0 + 3600 else -50)   # 10 m: 5 dB apart but 2.5 h of gap in between
_st = _cd.noise_steps(_c0 - 60)
assert [x["band"] for x in _st] == ["20m"] and abs(_st[0]["ts"] - _ts_step) <= 120 and abs(_st[0]["delta"] - 2.0) < 0.3, _st      # 30 m flat; 10 m is a gap, not a step
_e1 = _cd.add_event(_ts_step - 4000, "wire on"); _e2 = _cd.add_event(_ts_step, "LED on"); _e3 = _cd.add_event(_ts_step + 3000, "LED off")
_ef = _cd.event_effect(_e2, before_min=30, settle_min=6, after_min=30, now=_c0 + 4 * 3600)
_bd2 = {b["band"]: b for b in _ef["bands"]}
assert abs(_bd2["20m"]["delta"] - 2.0) < 0.3 and _bd2["20m"]["clear"] and abs(_bd2["20m"]["rms_delta"] - 2.0) < 0.01, _bd2
assert abs(_bd2["30m"]["delta"]) < 0.3 and not _bd2["30m"]["clear"] and "10m" not in _bd2, _bd2
assert _ef["a"][0] >= _ts_step - 4000 + 360 and _ef["b"][1] <= _ts_step + 3000                     # windows stop at the neighbouring events
assert _cd.event_effect(99999) is None
_ef2 = _cd.event_effect(_e3, before_min=30, settle_min=6, after_min=30, now=_ts_step + 3000 + 300)   # nothing recorded after the last event yet
assert _ef2["bands"] == [], _ef2
print("before/after and noise shifts: ok")
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
import urllib.error                                 # events through the web page: the custom header is required, text is cleaned
def _post(path, obj, hdr=True):
    rq = urllib.request.Request(u + path, data=json.dumps(obj).encode(), method="POST", headers={"Content-Type": "application/json", **({"X-WSM": "1"} if hdr else {})})
    return urllib.request.urlopen(rq)
try:
    _post("api/event", {"text": "no header"}, hdr=False); raise SystemExit("POST without header accepted")
except urllib.error.HTTPError as _e:
    assert _e.code == 403
for _bad in ({"text": ""}, {"text": "x", "ts": 1}, {"text": "x", "ts": time.time() + 99999}):
    try:
        _post("api/event", _bad); raise SystemExit("bad event accepted")
    except urllib.error.HTTPError as _e:
        assert _e.code == 400
_r = json.loads(_post("api/event", {"text": "fan off", "ts": time.time() - 120}).read()); assert _r["ok"]
_h = json.loads(urllib.request.urlopen(u + "api/history?hours=1").read())
assert [e["text"] for e in _h["events"]] == ["fan off"] and "base" in _h and _h["events"][0]["id"] == _r["id"]
assert json.loads(_post("api/event/delete", {"id": _r["id"]}).read())["deleted"] == 1
assert json.loads(urllib.request.urlopen(u + "api/history?hours=1").read())["events"] == []
assert "bands" in json.loads(urllib.request.urlopen(u + "api/missed?hours=24").read())
assert "evAdd" in wsm_web.PAGE and "relN" in wsm_web.PAGE and "loadMiss" in wsm_web.PAGE
print("events, typical noise and missed API: ok")
try:
    urllib.request.urlopen(u + "api/effect?id=999999"); raise SystemExit("effect of an unknown event answered")
except urllib.error.HTTPError as _e:
    assert _e.code == 404
assert "steps" in json.loads(urllib.request.urlopen(u + "api/steps?hours=24").read())
assert "loadEffect" in wsm_web.PAGE and "loadSteps" in wsm_web.PAGE and "axf" in wsm_web.PAGE and "dec:true" in wsm_web.PAGE
assert "const PMIN=" in wsm_web.PAGE and ".panel.min>:not(h2)" in wsm_web.PAGE
print("effect and steps API: ok")
print("TMP", tmp, "PORT", cfg["web"]["port"])
if os.environ.get("WSM_KEEP"):
    time.sleep(int(os.environ["WSM_KEEP"]))
