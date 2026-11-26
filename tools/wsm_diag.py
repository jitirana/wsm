#!/usr/bin/env python3
"""Why so few spots? A quick look at the station from its own database, plus a frequency check against WSPRnet.
usage: python3 tools/wsm_diag.py [-c ~/wsm/wsm.yaml] [--days 2] [--no-net]

1. Per band and antenna over the last --days: slots listened, spots, median noise in the WSPR window, highest input peak, gain.
   Many slots, 0 spots and a normal noise = band closed or antenna weak there; a noise far below the other bands = the antenna
   (or the Hi-Z port) delivers almost nothing on that band.
2. Frequency check: for the stations this receiver decoded (last 7 days, up to 15), the frequency the other receivers reported
   for the same transmission (wspr.live). The difference, in Hz and in ppm of the RF frequency, is the error of the RSP2
   reference. WSPR signals spread over +-100 Hz and the decoder searches about +-150 Hz, so an error grows with the band:
   2 ppm is 28 Hz on 20 m but 56 Hz on 10 m, enough to push the edge of the band out of the search there.
3. Timing: median DT of the spots (should be within +-1 s).
"""
import argparse
import json
import os
import sqlite3
import statistics as st
import sys
import time
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import wsm_config            # noqa: E402
import wsm_openings as op    # noqa: E402


def band_table(con, since):
    rows = con.execute("SELECT band, COALESCE(antenna,'?'), COUNT(*), SUM(n_spots), MAX(peak), MIN(gain_db), MAX(gain_db), "
                       "GROUP_CONCAT(noise_db) FROM slots WHERE ts>=? AND status!='error' GROUP BY 1, 2 ORDER BY 1, 2", (since,)).fetchall()
    out = []
    for band, ant, n, spots, peak, g0, g1, noises in rows:
        nz = [float(v) for v in (noises or "").split(",") if v not in ("", None)]
        out.append(dict(band=band, antenna=ant, slots=n, spots=spots or 0, noise=round(st.median(nz), 1) if nz else None,
                        peak=round(100 * (peak or 0)), gain=g0 if g0 == g1 else f"{g0}-{g1}"))
    return out


def freq_query(band, ts, call):
    t = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(ts))
    return (f"SELECT rx_sign, frequency FROM wspr.rx WHERE band = {op.BAND_CODE[band]} AND time = '{t}' "
            f"AND tx_sign = '{op._call_ok(call)}' FORMAT JSON")


def others_freq(text, my_call):
    """median frequency (Hz) the other receivers reported, or None"""
    f = [float(r["frequency"]) for r in json.loads(text).get("data", [])
         if r.get("frequency") and str(r.get("rx_sign", "")).upper() != my_call.upper()]
    return (st.median(f), len(f)) if f else (None, 0)


def http(sql, url="https://db1.wspr.live/", timeout=60):
    req = urllib.request.Request(url + "?" + urllib.parse.urlencode({"query": sql}), headers={"User-Agent": op.UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("-c", "--config", default="~/wsm/wsm.yaml")
    ap.add_argument("--days", type=float, default=2.0)
    ap.add_argument("--no-net", action="store_true", help="skip the frequency check against wspr.live")
    a = ap.parse_args()
    cfg = wsm_config.load(os.path.expanduser(a.config))
    my_call = cfg["station"]["callsign"]
    con = sqlite3.connect("file:%s?mode=ro" % os.path.join(cfg["data_dir"], "wsm.sqlite"), uri=True)
    now = time.time()

    print(f"== Bands, last {a.days:g} days (noise = median noise in the WSPR window, dB re full scale in 2500 Hz)")
    print(f"{'band':5} {'ant':4} {'slots':>5} {'spots':>5} {'noise':>6} {'peak%':>5} {'gain':>6}")
    for r in band_table(con, now - a.days * 86400):
        print(f"{r['band']:5} {r['antenna']:4} {r['slots']:5} {r['spots']:5} {str(r['noise']):>6} {r['peak']:5} {str(r['gain']):>6}")

    sp = con.execute("SELECT ts, band, call, freq_hz, snr, dt FROM spots WHERE ts>=? ORDER BY ts DESC LIMIT 15", (now - 7 * 86400,)).fetchall()
    sp = [s for s in sp if s[2] and not s[2].startswith("<") and s[1] in op.BAND_CODE]
    if sp:
        dts = [s[5] for s in sp if s[5] is not None]
        print(f"\n== Timing: median DT of the last {len(dts)} spots {st.median(dts):+.1f} s (should be within +-1 s)" if dts else "")
    if a.no_net or not sp:
        if not sp:
            print("\nNo decoded station in the last 7 days: nothing to check the frequency with.")
        return
    print("\n== Frequency check against WSPRnet (the same transmission as reported by the other receivers)")
    print(f"{'UTC':16} {'band':4} {'call':10} {'mine Hz':>12} {'others Hz':>12} {'n':>3} {'diff Hz':>8} {'ppm':>6}")
    ppms = []
    for ts, band, call, fhz, snr, dt in sp:
        try:
            ref, n = others_freq(http(freq_query(band, ts, call)), my_call)
        except Exception as e:
            print(f"{time.strftime('%Y-%m-%d %H:%M', time.gmtime(ts))} {band:4} {call:10} wspr.live: {e}")
            continue
        if ref is None:
            print(f"{time.strftime('%Y-%m-%d %H:%M', time.gmtime(ts))} {band:4} {call:10} {fhz:12.0f} {'(nobody else)':>12}")
        else:
            d = fhz - ref
            ppms.append(d / ref * 1e6)
            print(f"{time.strftime('%Y-%m-%d %H:%M', time.gmtime(ts))} {band:4} {call:10} {fhz:12.0f} {ref:12.0f} {n:3} {d:+8.1f} {ppms[-1]:+6.2f}")
        time.sleep(3)
    if ppms:
        m = st.median(ppms)
        print(f"\nMedian error {m:+.2f} ppm: {m * 14.0956:+.0f} Hz on 20 m, {m * 7.0386:+.0f} on 40 m, {m * 10.1387:+.0f} on 30 m, "
              f"{m * 28.1246:+.0f} on 10 m.")
        print("Within about 1 ppm is fine. More than that: the RSP2 reference is off and needs the `ppm` correction in wsm.yaml." if abs(m) > 1
              else "The frequency is fine: it does not explain the missing spots.")


if __name__ == "__main__":
    main()
