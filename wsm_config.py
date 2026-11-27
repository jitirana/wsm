"""wsm_config.py - configuration (wsm.yaml) with defaults, and the table of WSPR dial frequencies."""
import copy
import os

import yaml

# WSPR dial frequencies (USB, Hz): the WSPR window is dial + 1400 ... 1600 Hz
BANDS = {
    "160m": 1836600, "80m": 3568600, "60m": 5287200, "40m": 7038600, "30m": 10138700, "20m": 14095600,
    "17m": 18104600, "15m": 21094600, "12m": 24924600, "10m": 28124600,
    "6m": 50293000, "4m": 70091000, "2m": 144489000, "70cm": 432300000,
}

DEFAULTS = dict(
    station=dict(callsign="PU2TKN", grid="HI03"),
    data_dir="~/wsm/data",                 # SQLite database, decoder scratch files
    decoder=dict(path="~/wsm/decoder/wsm_decode", hashtable=False,
                 engine="wsjtx",              # wsjtx: the WSJT-X wsprd (more sensitive); legacy: wsm_decode (rtlsdr-wsprd), also the fallback
                 wsjtx_path="",               # default: wsprd_wsjtx next to wsm_decode
                 flags=[]),                   # extra wsprd options for the wsjtx engine, e.g. ["-d"]
    web=dict(enabled=True, host="127.0.0.1", port=8091, token="",
             public=False, url_key=False),              # True: visitors without the key see spots, map and waterfall (not health, not CSV)
    landing=dict(                          # station landing page, served when the request comes for one of `hosts`
        enabled=False, hosts=[], title="", about="", equipment=[], links=[], projects=[],
        monitor_url="", adsb_url=""),
    adsb=dict(json_dir="/run/readsb"),     # readsb JSON files, for the ADS-B numbers of the landing page
    keep_days=0,                           # 0 = keep every spot
    dedup_same_call=True,                  # same callsign+locator several times in a slot: keep only the best SNR
    store_own_spots=False,                 # False: spots of the station's own callsign (TX = RX) are not stored
    waterfall=True,                        # picture of the last slot of each band (RAM only)
    radios=[dict(
        name="rsp2", driver="sdrplay",
        sample_rate=62500,                 # S/s requested from the driver (decimated by the RSP itself); anything >= 31250 works
        if_bandwidth=200000,               # RSP2 IF filter (Hz)
        offset_hz=10000,                   # tune this far above dial+1500 Hz and shift back in software (keeps the DC spike out)
        antenna="A",                       # A / B / HIZ  (A and B are 50 ohm, HIZ is 1 kohm 10 kHz-30 MHz)
        gain_db=30.0,                      # overall gain, AGC off. Raise/lower it so that `peak` stays well below 1.0
        ppm=0.0,
        clock_offset_s=0.0,                # added to the time stamps (calibrate it with a GPS-timed transmitter)
        rotation=["20m", "40m", "30m", "10m"],   # bands in turn, one 2-minute slot each; repeat a band to give it more time
        bands={},                          # per band overrides: {"40m": {"antenna": "A", "gain_db": 25}}
    )],
)


def _merge(base, over):
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _merge(base[k], v)
        else:
            base[k] = v
    return base


def load(path=None):
    cfg = copy.deepcopy(DEFAULTS)
    if path:
        with open(os.path.expanduser(path)) as f:
            user = yaml.safe_load(f) or {}
        radios = user.pop("radios", None)
        _merge(cfg, user)
        if radios is not None:
            cfg["radios"] = [_merge(copy.deepcopy(DEFAULTS["radios"][0]), r) for r in radios]
    for r in cfg["radios"]:
        for b in r["rotation"]:
            check_entry(b, f"the rotation of radio {r['name']}")
        r["_schedule"] = parse_schedule(r.get("schedule"), r["name"])
    cfg["data_dir"] = os.path.expanduser(cfg["data_dir"])
    cfg["decoder"]["path"] = os.path.expanduser(cfg["decoder"]["path"])
    d = cfg["decoder"]
    d["wsjtx_path"] = os.path.expanduser(d.get("wsjtx_path") or os.path.join(os.path.dirname(d["path"]), "wsprd_wsjtx"))
    d["engine"] = str(d.get("engine") or "wsjtx").lower()
    if d["engine"] not in ("wsjtx", "legacy"):
        raise SystemExit(f"decoder.engine must be wsjtx or legacy, not {d['engine']!r}")
    return cfg


# ---------- rotation by time of day ----------
# radios[].schedule (optional), UTC time ranges -> rotation; the first range that contains the slot time wins,
# outside every range the plain `rotation` is used. "21-09" wraps over midnight; "00-24" (or "00-00") is the whole day.
#   schedule:
#     "09-21": [20m, 20m, 10m, 17m]          # day
#     "21:30-09": [40m, 30m, 20m, 80m]       # night (HH or HH:MM)

def _minute(txt, name):
    txt = str(txt).strip()
    try:
        hh, mm = (txt.split(":") + ["0"])[:2]
        m = int(hh) * 60 + int(mm)
    except ValueError:
        raise SystemExit(f"radio {name}: bad time {txt!r} in schedule (use HH or HH:MM, UTC)")
    if not 0 <= m <= 1440:
        raise SystemExit(f"radio {name}: time {txt!r} out of range in schedule")
    return m


ANTENNAS = ("A", "B", "HIZ")


def split_entry(entry):
    """"20m" -> ("20m", None);  "20m@HIZ" -> ("20m", "HIZ"): a rotation entry may pick the antenna input for that slot"""
    band, _, ant = str(entry).partition("@")
    return band.strip(), (ant.strip().upper() or None) if _ else None


def check_entry(entry, where):
    band, ant = split_entry(entry)
    if band not in BANDS:
        raise SystemExit(f"unknown band {band!r} in {where} (known: {', '.join(BANDS)})")
    if ant is not None and ant not in ANTENNAS:
        raise SystemExit(f"unknown antenna {ant!r} in {where} (use {', '.join(ANTENNAS)}, e.g. 20m@HIZ)")


def parse_schedule(sched, name="radio"):
    """{"HH[:MM]-HH[:MM]": [bands]} -> [(start_min, end_min, [bands])], validated"""
    out = []
    for rng, bands in (sched or {}).items():
        if "-" not in str(rng):
            raise SystemExit(f"radio {name}: schedule key {rng!r} must be a UTC range like \"09-21\"")
        a, b = str(rng).split("-", 1)
        if isinstance(bands, str):
            bands = [bands]
        if not bands:
            raise SystemExit(f"radio {name}: empty band list for {rng!r} in schedule")
        for band in bands:
            check_entry(band, f"the schedule {rng!r} of radio {name}")
        out.append((_minute(a, name) % 1440, _minute(b, name) % 1440, list(bands)))
    return out


def rotation_at(rcfg, ts):
    """rotation in force for a slot starting at unix time ts (UTC)"""
    m = int(ts) % 86400 // 60
    for a, b, bands in rcfg.get("_schedule") or []:
        if a == b or (a < b and a <= m < b) or (a > b and (m >= a or m < b)):
            return bands
    return rcfg["rotation"]


def all_bands(rcfg):
    """every band a radio can visit (plain rotation + schedule), in order of first appearance; "20m@HIZ" counts as 20m"""
    out = []
    for e in list(rcfg["rotation"]) + [x for _, _, bands in rcfg.get("_schedule") or [] for x in bands]:
        b = split_entry(e)[0]
        if b not in out:
            out.append(b)
    return out
