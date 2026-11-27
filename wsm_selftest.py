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
import wsm_sys                                     # thermal health per day: slowed minutes, short slots while slowed, old databases
_tz = time.gmtime
_td = wsm_db.DB(os.path.join(tmp, "therm.sqlite"), "HI03"); _t0 = (int(time.time()) // 86400 - 2) * 86400
for _k in range(12):                                 # one hour of day 1: 3 rows slowed 40 %, 1 row under-voltage 20 %, the rest normal
    _td.add_sys(dict(ts=_t0 + _k * 300, temp_c=60.0, temp_max=62.0 + _k, clock_mhz=1350, clock_min=1200, samples=30,
                     uv_pct=20.0 if _k == 7 else 0.0, low_clock_pct=0.0, capped_pct=40.0 if _k in (2, 3, 4) else 0.0))
_td.add_sys(dict(ts=_t0 + 86400, temp_c=50.0, temp_max=51.0, clock_mhz=1400, clock_min=1400, samples=30, uv_pct=None, low_clock_pct=0.0))
for _k, _note in ((0, ""), (3, "slot covered 80 %"), (5, "slot covered 85 %"), (20, "slot covered 70 %")):
    _td.add_slot(_t0 + _k * 120, "20m", 14095600, "rsp2", "HIZ", 30, 62500, 0.1, -30, "ok", _note, -50.0)
_dd = _td.thermal_daily(_t0 - 1, tz=_tz)
assert [x["day"] for x in _dd] == [time.strftime("%Y-%m-%d", _tz(_t0 + 86400)), time.strftime("%Y-%m-%d", _tz(_t0))], _dd
_a, _b = _dd[1], _dd[0]
assert _a["capped_min"] == 6.0 and _a["uv_min"] == 1.0 and _a["temp_max"] == 73.0 and _a["minutes"] == 60.0, _a
assert _a["slots"] == 4 and _a["short"] == 3 and _a["short_slow"] == 1, _a      # short slots at 6, 10 and 40 min: only the one at 10 min falls in a slowed row
assert _b["capped_min"] is None and _b["uv_min"] is None and _b["slots"] == 0, _b    # a row without the new data shows "–"
_m = wsm_sys.SysMon(_td, clock=lambda: 0); _m.samples.extend([(_t0 + 2 * 86400 + i, 55.0, 1400.0, 0x0 if i < 6 else 0x8) for i in range(10)])
_m.save_history(_t0 + 2 * 86400)
assert _td.con.execute("SELECT capped_pct, uv_pct FROM syshist WHERE ts=?", (_t0 + 2 * 86400,)).fetchone()[:] == (40.0, 0.0)
_oc = sqlite3.connect(os.path.join(tmp, "oldsys.sqlite"))
_oc.execute("CREATE TABLE syshist (ts INTEGER PRIMARY KEY, temp_c REAL, temp_max REAL, clock_mhz REAL, clock_min REAL, uv_pct REAL, low_clock_pct REAL, samples INTEGER)")
_oc.execute("INSERT INTO syshist VALUES (1000, 50, 51, 1400, 1400, 0, 0, 30)"); _oc.commit(); _oc.close()
_od = wsm_db.DB(os.path.join(tmp, "oldsys.sqlite"), "HI03"); assert _od.thermal_daily(0)[0]["capped_min"] is None
_th = json.loads(urllib.request.urlopen(u + "api/thermal?days=3").read()); assert "days" in _th
assert "loadTherm" in wsm_web.PAGE and 'id="thermBox"' in wsm_web.PAGE
print("thermal health per day: ok")
_rs = np.random.default_rng(11); _N = 45000; _tq = np.arange(_N) / 375.0      # narrow spurs: a carrier is found, WSPR signals are not
_nz = (_rs.normal(size=_N) + 1j * _rs.normal(size=_N)) * 2e-3 / np.sqrt(2); _n0 = (2e-3) ** 2 / 375.0
_amp = lambda snr: np.sqrt(10 ** (snr / 10) * _n0 * 2500)
def _wspr(f, snr):
    _sy = _rs.integers(0, 4, 162); _i = np.clip(((_tq - 1) / (256 / 375)).astype(int), 0, 161)
    return _amp(snr) * np.exp(2j * np.pi * np.cumsum(f + (_sy[_i] - 1.5) * 375 / 256) / 375) * ((_tq >= 1) & (_tq < 1 + 162 * 256 / 375))
_x = (_nz + _amp(-24) * np.exp(2j * np.pi * 37.3 * _tq) + _wspr(-50, 8) + _wspr(60, -18)).astype(np.complex64)
_sp = wsm_dsp.find_spurs(_x)
assert len(_sp) == 1 and abs(_sp[0]["off_hz"] - 37.3) < 0.1 and abs(_sp[0]["snr"] + 24) < 1.5, _sp
assert wsm_dsp.find_spurs(_nz.astype(np.complex64)) == [] and wsm_dsp.find_spurs(np.zeros(_N, np.complex64)) is None
_sd = wsm_db.DB(os.path.join(tmp, "spur.sqlite"), "HI03"); _s0 = int(time.time()) // 120 * 120 - 7200
for _k in range(10):                                  # 10 slots of 20 m: a carrier in 8 of them (drifting 0.2 Hz), a one-off in 1
    _sid = _sd.add_slot(_s0 + _k * 120, "20m", 14095600, "rsp2", "HIZ", 30, 62500, 0.1, -30, "ok", "", -50.0)
    _l = [dict(off_hz=37.3 + 0.02 * _k, snr=-24.0 - _k % 3, above=9.0, persist=100)] if _k < 8 else []
    if _k == 9:
        _l = [dict(off_hz=-80.0, snr=-15.0, above=20.0, persist=100)]
    if _l:
        _sd.add_spurs(_sid, _s0 + _k * 120, "20m", 14095600, _l)
_ss = _sd.spur_summary(_s0 - 1)
assert len(_ss) == 2 and _ss[0]["slots"] == 8 and _ss[0]["pct"] == 80 and abs(_ss[0]["freq_hz"] - 14097137.4) < 0.3, _ss
assert _ss[0]["snr_max"] == -24.0 and _ss[1]["slots"] == 1, _ss
assert "spurs" in json.loads(urllib.request.urlopen(u + "api/spurs?hours=24").read())
assert "loadSpurs" in wsm_web.PAGE and 'id="spurBox"' in wsm_web.PAGE and "span.spur" in wsm_web.PAGE
print("narrow spurs: ok")
import copy, socket, http.client                   # web hardening: limits, lockout scope, odd numbers, headers, CSV cells
_hc = copy.deepcopy(cfg); _hp = cfg["web"]["port"] + 7
_hc["web"].update(port=_hp, token="t" * 32, public=True)
_old_t, _old_m = wsm_web.Handler.timeout, wsm_web.Server.MAX_CONN
wsm_web.Handler.timeout, wsm_web.Server.MAX_CONN = 1.5, 4
_hs = wsm_web.start(_hc, db, state, log=lambda m: None)
_hu = f"http://127.0.0.1:{_hp}/"
def _get(path, hdr=None):
    c = http.client.HTTPConnection("127.0.0.1", _hp, timeout=10); c.request("GET", path, headers=hdr or {}); r = c.getresponse(); r.read(); c.close()
    return r
_r = _get("/api/summary?hours=inf"); assert _r.status == 200, _r.status
assert _get("/api/spots?limit=1e309").status == 200 and _get("/").getheader("Server") == "WSM"
_c = http.client.HTTPConnection("127.0.0.1", _hp, timeout=5); _c.request("PUT", "/"); assert _c.getresponse().status == 405; _c.close()
for _i in range(8):
    assert _get("/api/history", {"X-WSM-Key": f"wrong{_i}"}).status == 401
assert _get("/").status == 200 and _get("/api/spots").status == 200           # public view stays open while locked out
assert _get("/api/history", {"X-WSM-Key": "t" * 32}).status == 429             # key attempts are refused
_hs.fails.clear()
def _post2(path, obj, cookie=None):
    c = http.client.HTTPConnection("127.0.0.1", _hp, timeout=10); b = json.dumps(obj)
    h = {"X-WSM": "1", "Content-Type": "application/json"}
    if cookie:
        h["Cookie"] = cookie
    c.request("POST", path, body=b, headers=h); r = c.getresponse(); r.read(); c.close(); return r
assert _get("/login").status == 200 and b"current-password" in wsm_web.LOGIN_PAGE.encode()
assert _get("/api/history", {"X-WSM-Key": "t" * 32}).status == 200             # scripts: key in a header, data at once
assert _get("/?key=" + "t" * 32).getheader("Location") == "/login"              # ?key= is off by default
assert _post2("/api/login", {"key": "nope"}).status == 401
_lr = _post2("/api/login", {"key": "t" * 32}); assert _lr.status == 200
_ck = _lr.getheader("Set-Cookie").split(";")[0]; assert "HttpOnly" in _lr.getheader("Set-Cookie")
assert _get("/api/history", {"Cookie": _ck}).status == 200
_ck2 = _post2("/api/login", {"key": "t" * 32}).getheader("Set-Cookie").split(";")[0]   # a second browser
_lo = _post2("/api/logout", {}, _ck); assert _lo.status == 200 and "Max-Age=0" in _lo.getheader("Set-Cookie")
assert _get("/api/history", {"Cookie": _ck2}).status == 200                    # plain log out: the other browser stays in
assert _post2("/api/logout", {"all": True}, _ck2).status == 200
assert _get("/api/history", {"Cookie": _ck2}).status == 401 and _get("/api/history", {"Cookie": _ck}).status == 401
_ck3 = _post2("/api/login", {"key": "t" * 32}).getheader("Set-Cookie").split(";")[0]
assert _ck3 != _ck and _get("/api/history", {"Cookie": _ck3}).status == 200
assert open(os.path.join(_hc["data_dir"], "web_session")).read().strip() and oct(os.stat(os.path.join(_hc["data_dir"], "web_session")).st_mode)[-3:] == "600"
_hs.fails.clear()
_sk = socket.create_connection(("127.0.0.1", _hp)); _sk.sendall(b"GET / HTTP/1.1\r\nHost: x\r\n")   # never ends the headers
_sk.settimeout(5); _t0 = time.time()
assert _sk.recv(100) == b"" and time.time() - _t0 < 4, "half-open connection not closed"
_sk.close()
_held = [socket.create_connection(("127.0.0.1", _hp)) for _ in range(4)]       # fill every slot
time.sleep(0.3)
_x = socket.create_connection(("127.0.0.1", _hp)); _x.settimeout(5); _x.sendall(b"GET / HTTP/1.1\r\nHost: x\r\n\r\n")
assert _x.recv(100).startswith(b"HTTP/1.1 503"), "no 503 when full"
_x.close()
for _k in _held:
    _k.close()
time.sleep(0.5); assert _get("/favicon.svg").status == 200                     # slots come back
_hs.shutdown(); _hs.server_close()
wsm_web.Handler.timeout, wsm_web.Server.MAX_CONN = _old_t, _old_m
assert wsm_web._cell("=HYPERLINK(1)") == "'=HYPERLINK(1)" and wsm_web._cell("PU2TKN") == "PU2TKN" and wsm_web._cell(None) == ""
_hc2 = copy.deepcopy(_hc); _hc2["web"].update(port=_hp + 1, public=False)        # private mode: "/" leads to the form
_hs2 = wsm_web.start(_hc2, db, state, log=lambda m: None)
_c2 = http.client.HTTPConnection("127.0.0.1", _hp + 1, timeout=10); _c2.request("GET", "/"); _r2 = _c2.getresponse(); _r2.read()
assert _r2.status == 303 and _r2.getheader("Location") == "/login"; _c2.close()
_hs2.shutdown(); _hs2.server_close()
_hs3 = wsm_web.start(_hc, db, state, log=lambda m: None)                       # CSP: no inline scripts but the page's own
for _path, _html in (("/", wsm_web.PAGE), ("/login", wsm_web.LOGIN_PAGE), ("/about", open(os.path.join(here, "static", "about.html")).read())):
    _csp = _get(_path).getheader("Content-Security-Policy"); _sc = _csp.split("script-src")[1].split(";")[0]
    assert "unsafe-inline" not in _sc and wsm_web.script_hashes(_html) in _sc and "'sha256-" in _sc, (_path, _csp)
    assert "object-src 'none'" in _csp and "frame-ancestors 'none'" in _csp and "base-uri 'none'" in _csp
for _f in ("station.html", "about.html"):
    _t = open(os.path.join(here, "static", _f)).read()
    assert _t.count("<script") == len(wsm_web.script_hashes(_t).split()) + _t.count("<script src"), _f   # every inline script hashed
_hs3.shutdown(); _hs3.server_close()
assert wsm_web.script_hashes("<script>a</script><script src=x></script>") == "'sha256-ypeBEsobvcr6wjGzmiPcTaeG7/gUfE5yuYB3ha/uSLs='"
print("web limits, login and log out: ok")
sys.path.insert(0, os.path.join(here, "tools")); import wsm_landing_snapshot as _ls   # landing page without WSM (lighttpd copy)
_lc = copy.deepcopy(cfg); _lc["landing"] = dict(enabled=True, title="T", about="A", equipment=["E"], monitor_url="https://m/")
_out = os.path.join(tmp, "station_static"); _ld = _ls.publish(_lc, _out)
assert sorted(os.listdir(_out)) == ["favicon.svg", "landing.json", "station.html"] and _ld["wsm_offline"] is True
_lj = json.load(open(os.path.join(_out, "landing.json")))
assert _lj["wspr"]["spots_24h"] is None and _lj["adsb"] == {"ok": False} and _lj["about"] == "A" and "token" not in json.dumps(_lj)
assert _ls.snapshot(_lc, db, online=True)["wsm_offline"] is False
_sh = open(os.path.join(here, "static", "station.html")).read()
assert 'id="wOff"' in _sh and "/landing.json" in _sh and "/tar1090/data/aircraft.json" in _sh
print("landing page without WSM: ok")
print("TMP", tmp, "PORT", cfg["web"]["port"])
if os.environ.get("WSM_KEEP"):
    time.sleep(int(os.environ["WSM_KEEP"]))
