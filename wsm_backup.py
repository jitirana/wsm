#!/usr/bin/env python3
"""wsm_backup.py - daily backup of everything needed to rebuild WSM from scratch.

Each backup is ONE archive  wsm-backup-YYYYmmdd-HHMM.tar.gz  (optionally encrypted: .tar.gz.enc) containing
  db/wsm.sqlite        consistent snapshot of the database (SQLite online-backup API, safe while the service writes)
  tree/<wsm dir>/      all code, wsm.yaml, static files, the decoder SOURCE, deploy files (no caches, no objects, no old data)
  data_state/*.json    small state files (wsprnet, daily report, health)
  system/...           systemd units (wsm, sbm), udev rules of the SDRplay, copied when readable
  extra/...            anything listed in backup.extra_paths / extra_files (e.g. the SBM directory)
  MANIFEST.txt         OS, kernel, python, pip freeze, apt packages, SDRplay API files, timezone
  RESTORE.md           step by step to recreate the system on a new Ubuntu VM
The archive holds wsm.yaml, i.e. SECRETS (ntfy topic, web key): it is created 600 inside a 700 directory. Before copying it off this
machine, switch encryption on (backup.encrypt_passphrase_file).

wsm.yaml (all optional):
  backup:
    enabled: yes
    time: "03:30"                     # local time of the daily backup
    timezone: America/Fortaleza
    keep: 14                          # newest N backups are kept
    dir: ""                           # default: <data_dir>/backups
    extra_paths: []                   # more directories to include, e.g. [~/sbm]
    max_file_mb: 5                    # files bigger than this are left out (listed in MANIFEST.txt)
    max_archive_mb: 500               # a backup bigger than this is discarded and reported (protects the disk)
    exclude_dirs: []                  # whole directories to leave out: full paths (~/sbm/callisto) or plain names (callisto)
    exclude: [...]                    # file name patterns left out: *.wav *.png *.jpg *.cf64 *.log ... (see DEFAULTS)
    extra_files: []                   # more single files
    encrypt_passphrase_file: ""       # file (chmod 600) holding a passphrase -> AES-256 via openssl; empty = no encryption
    notify: yes                       # ntfy when a backup fails (and when it works again)

CLI:  python3 wsm_backup.py --now | --list | --verify FILE | --restore FILE --to DIR
"""
import argparse
import datetime as dt
import fnmatch
import glob
import json
import os
import platform
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import threading
import time

import wsm_config

DEFAULTS = dict(enabled=True, time="03:30", timezone="America/Fortaleza", keep=14, dir="", extra_paths=[], extra_files=[],
                encrypt_passphrase_file="", notify=True, max_file_mb=5, max_archive_mb=500, exclude_dirs=[],
                exclude=["*.wav", "*.png", "*.jpg", "*.jpeg", "*.gif", "*.webp", "*.cf64", "*.cu8", "*.cs16", "*.iq", "*.raw", "*.mp4",
                         "*.zip", "*.tgz", "*.tar", "*.tar.gz", "*.log", "*.sqlite", "*.sqlite-wal", "*.sqlite-shm"])
PREFIX = "wsm-backup"
SKIP_DIRS = {"__pycache__", ".venv", "backups", ".git", "node_modules", "site-packages"}
SKIP_SUFFIX = (".pyc", ".o", ".tmp")
SKIP_NAMES = {"wsm_decode", "wsm_symbols"}                      # compiled binaries: rebuilt with `make`
SYSTEM_FILES = ["/etc/systemd/system/wsm.service", "/etc/systemd/system/sbm.service"]
SYSTEM_GLOBS = ["/etc/udev/rules.d/*sdrplay*", "/etc/udev/rules.d/*mirics*", "/etc/udev/rules.d/*rsp*"]


def settings(cfg):
    s = dict(DEFAULTS)
    s.update(cfg.get("backup") or {})
    s["enabled"] = str(s["enabled"]).lower() not in ("0", "false", "no", "off", "n")
    s["notify"] = str(s["notify"]).lower() not in ("0", "false", "no", "off", "n")
    s["keep"] = max(1, int(s["keep"]))
    s["dir"] = os.path.expanduser(str(s["dir"] or "")) or os.path.join(cfg["data_dir"], "backups")
    s["max_file_mb"] = float(s["max_file_mb"])
    s["max_archive_mb"] = float(s["max_archive_mb"])
    s["exclude_dirs"] = [os.path.expanduser(str(x)) for x in (s["exclude_dirs"] or [])]
    s["exclude"] = [str(x) for x in (s["exclude"] or [])]
    s["extra_paths"] = [os.path.expanduser(str(p)) for p in (s["extra_paths"] or [])]
    s["extra_files"] = [os.path.expanduser(str(p)) for p in (s["extra_files"] or [])]
    s["encrypt_passphrase_file"] = os.path.expanduser(str(s["encrypt_passphrase_file"] or ""))
    return s


def base_dir():
    return os.path.dirname(os.path.abspath(__file__))


def tz(name):
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(name)
    except Exception:
        return dt.timezone(dt.timedelta(hours=-3))


def sh(cmd, timeout=20):
    try:
        r = subprocess.run(cmd, shell=isinstance(cmd, str), capture_output=True, text=True, timeout=timeout)
        return (r.stdout + (("\n[stderr] " + r.stderr.strip()) if r.returncode and r.stderr.strip() else "")).strip()
    except Exception as e:
        return f"(not available: {type(e).__name__})"


# ---------- pieces of the archive ----------
def snapshot_db(src, dst):
    """consistent copy of a live SQLite database + integrity check; returns the number of spots"""
    a = sqlite3.connect(f"file:{src}?mode=ro", uri=True, timeout=60)
    b = sqlite3.connect(dst)
    try:
        a.backup(b)
        if b.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("integrity_check of the snapshot failed")
        return b.execute("SELECT COUNT(*) FROM spots").fetchone()[0]
    finally:
        b.close()
        a.close()


def manifest(cfg, n_spots):
    osr = sh("grep -E '^(PRETTY_NAME|VERSION_ID)=' /etc/os-release")
    lines = [
        f"WSM backup  {time.strftime('%Y-%m-%d %H:%M:%S %z')}",
        f"station     {cfg['station']['callsign']} {cfg['station']['grid']}",
        f"host        {platform.node()}   user {os.environ.get('USER', '?')}   wsm dir {base_dir()}",
        f"database    {n_spots} spots", "", "== OS ==", osr, sh("uname -srm"), sh("timedatectl show -p Timezone -p NTPSynchronized"),
        "", f"== Python ==  {sys.version.split()[0]}  ({sys.executable})", "", "== SDRplay / Soapy ==",
        sh("ls -l /usr/local/lib/libsdrplay_api* /usr/local/include/sdrplay_api.h /opt/sdrplay_api 2>/dev/null"),
        sh("grep -m1 -i 'SDRPLAY_API_VERSION' /usr/local/include/sdrplay_api.h"),
        sh("systemctl is-enabled sdrplay.service"), sh("dpkg -l 2>/dev/null | grep -i -E 'soapy|sdrplay|fftw3|cloudflared' | awk '{print $2, $3}'"),
        "", "== apt manually installed ==", sh("apt-mark showmanual | tr '\\n' ' '", 30),
        "", "== cloudflared ==", sh("systemctl is-enabled cloudflared.service"), "(token/credentials are NOT in this backup: re-issue them in the Cloudflare dashboard)",
        "", "== crontab ==", sh("crontab -l"),
    ]
    return "\n".join(lines) + "\n"


RESTORE = """# Rebuild WSM from this backup

Needs: a fresh Raspberry Pi OS / Debian / Ubuntu system (same major version as in MANIFEST.txt), the user that runs WSM,
working internet, the SDRplay RSP2 attached.

1. Base system
   sudo timedatectl set-timezone America/Fortaleza ; sudo timedatectl set-ntp true
   sudo apt update && sudo apt install -y build-essential libfftw3-dev python3-venv python3-pip sqlite3 libusb-1.0-0 \\
        soapysdr-tools python3-soapysdr cloudflared   # cloudflared from Cloudflare's apt repository
2. SDRplay API (proprietary, not stored here): download the version named in MANIFEST.txt from sdrplay.com, run the .run installer,
   then  sudo systemctl enable --now sdrplay ; install the SoapySDRPlay3 module (see MANIFEST.txt for the packages that were used).
3. Python environments: the venvs themselves are not stored, only `venvs/<path>/requirements.txt` (pip freeze). For each one:
   python3 -m venv --system-site-packages <path> && <path>/bin/pip install -r venvs/<path>/requirements.txt
   (On a Raspberry Pi the system packages are enough: see docs/INSTALL.md and install_pi.sh.)
4. Unpack this archive (decrypt first when it ends in .enc:  openssl enc -d -aes-256-cbc -pbkdf2 -pass file:PASSFILE -in X.enc -out X):
     python3 wsm_backup.py --restore ARCHIVE --to ~/restore      (or: mkdir ~/restore && tar -xzf ARCHIVE -C ~/restore)
5. Put the files back:
     cp -a ~/restore/wsm-backup/tree/wsm ~/wsm
     mkdir -p ~/wsm/data && cp ~/restore/wsm-backup/data_state/*.json ~/wsm/data/ 2>/dev/null
     cp ~/restore/wsm-backup/db/wsm.sqlite ~/wsm/data/wsm.sqlite
     chmod 600 ~/wsm/wsm.yaml ; chmod 700 ~/wsm/data
     sudo cp ~/restore/wsm-backup/system/etc/systemd/system/*.service /etc/systemd/system/ ; sudo systemctl daemon-reload
     sudo cp ~/restore/wsm-backup/system/etc/udev/rules.d/* /etc/udev/rules.d/ 2>/dev/null ; sudo udevadm control --reload
   Directories under extra/ go back to their original absolute paths.
6. Build the decoder:  cd ~/wsm/decoder && make
7. Cloudflare tunnel: in the Cloudflare dashboard (Zero Trust > Networks > Tunnels) open the tunnel, copy the install command with a
   token and run it on the new VM; the public hostname must point to http://localhost:8091 .
8. Start:  sudo systemctl enable --now wsm   (SBM stays disabled: it shares the RSP2, WSM conflicts with it)
   Check:  journalctl -u wsm -n 30 --no-pager   and   python3 ~/wsm/wsm_health.py --test
"""


def keep_file(path, name):
    return not (name in SKIP_NAMES or name.endswith(SKIP_SUFFIX) or ".pre_patch" in name)


def is_venv(path):
    return os.path.isfile(os.path.join(path, "pyvenv.cfg"))


def add_tree(tar, root, arcroot, skip_real=(), st=None, skipped=None, venvs=None, sizes=None):
    """adds the files of a directory tree; big files and the `exclude` patterns are left out and recorded in `skipped`"""
    st = st or dict(max_file_mb=5, exclude=[], exclude_dirs=[])
    ex_real = {os.path.realpath(x) for x in st["exclude_dirs"] if "/" in x}
    ex_names = {x for x in st["exclude_dirs"] if "/" not in x}
    limit = st["max_file_mb"] * 1e6
    for dp, dns, fns in os.walk(root):
        keep = []
        for d in dns:
            full = os.path.join(dp, d)
            if is_venv(full):                                   # a Python virtual environment: only its pip freeze is saved
                if venvs is not None:
                    venvs.append(full)
            elif d in ex_names or os.path.realpath(full) in ex_real:
                if skipped is not None:
                    skipped.append((0, full + "/ (exclude_dirs)", "directory"))
            elif d not in SKIP_DIRS and os.path.realpath(full) not in skip_real:
                keep.append(d)
        dns[:] = keep
        for fn in fns:
            p = os.path.join(dp, fn)
            if os.path.islink(p) or not keep_file(p, fn):
                continue
            try:
                size = os.path.getsize(p)
            except OSError:
                continue
            why = None
            if any(fnmatch.fnmatch(fn.lower(), pat.lower()) for pat in st["exclude"]):
                why = "pattern"
            elif size > limit:
                why = f"> {st['max_file_mb']:g} MB"
            if why:
                if skipped is not None:
                    skipped.append((size, p, why))
                continue
            tar.add(p, arcname=os.path.join(arcroot, os.path.relpath(p, root)), recursive=False)
            if sizes is not None:
                k = os.path.join(root, os.path.relpath(p, root).split(os.sep)[0])
                sizes[k] = sizes.get(k, 0) + size


def venv_report(venvs):
    if not venvs:
        return ""
    return "\n== Python virtual environments (not stored; their pip freeze is in venvs/) ==\n" + "\n".join(venvs) + "\n"


def skipped_report(skipped):
    if not skipped:
        return "\n== left out of this backup ==\nnothing\n"
    tot = sum(x[0] for x in skipped)
    lines = ["", "== left out of this backup (patterns or too big): %d files, %.1f MB ==" % (len(skipped), tot / 1e6)]
    by = {}
    for size, p, _ in skipped:
        d = os.path.dirname(p)
        by[d] = by.get(d, 0) + size
    lines += ["%9.1f MB  %s" % (v / 1e6, k) for k, v in sorted(by.items(), key=lambda kv: -kv[1])[:15]]
    lines += ["", "largest files:"] + ["%9.1f MB  %s  (%s)" % (sz / 1e6, p, why) for sz, p, why in sorted(skipped, reverse=True)[:15]]
    return "\n".join(lines) + "\n"


def encrypt(src, dst, passfile):
    r = subprocess.run(["openssl", "enc", "-aes-256-cbc", "-pbkdf2", "-salt", "-pass", "file:" + passfile, "-in", src, "-out", dst],
                       capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError("openssl failed: " + r.stderr.strip()[:200])


def decrypt(src, dst, passfile):
    r = subprocess.run(["openssl", "enc", "-d", "-aes-256-cbc", "-pbkdf2", "-pass", "file:" + passfile, "-in", src, "-out", dst],
                       capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError("decryption failed (wrong passphrase file?)")


def make_backup(cfg, now=None):
    """-> path of the new archive. Raises on any problem (nothing half-written is left behind)."""
    st = settings(cfg)
    os.makedirs(st["dir"], mode=0o700, exist_ok=True)
    os.chmod(st["dir"], 0o700)
    stamp = time.strftime("%Y%m%d-%H%M", now or time.localtime())
    final = os.path.join(st["dir"], f"{PREFIX}-{stamp}.tar.gz")
    work = tempfile.mkdtemp(prefix=".wsm-bk-", dir=st["dir"])
    try:
        dbfile = os.path.join(work, "wsm.sqlite")
        n = snapshot_db(os.path.join(cfg["data_dir"], "wsm.sqlite"), dbfile)
        plain = os.path.join(work, "backup.tar.gz")
        top = PREFIX + "/"
        with tarfile.open(plain, "w:gz") as tar:
            tar.add(dbfile, top + "db/wsm.sqlite")
            skipped, venvs, sizes = [], [], {}
            b = base_dir()
            add_tree(tar, b, top + "tree/" + os.path.basename(b), {os.path.realpath(cfg["data_dir"])}, st, skipped, venvs, sizes)
            for j in glob.glob(os.path.join(cfg["data_dir"], "*.json")):
                tar.add(j, top + "data_state/" + os.path.basename(j))
            files = SYSTEM_FILES + [f for g in SYSTEM_GLOBS for f in glob.glob(g)]
            for f in files:
                if os.path.isfile(f) and os.access(f, os.R_OK):
                    tar.add(f, top + "system" + f)
            for d in st["extra_paths"]:
                if os.path.isdir(d):
                    add_tree(tar, d, top + "extra" + os.path.abspath(d), {os.path.realpath(st["dir"])}, st, skipped, venvs, sizes)
            for f in st["extra_files"]:
                if os.path.isfile(f):
                    tar.add(f, top + "extra" + os.path.abspath(f))
            for v in venvs:
                py = os.path.join(v, "bin", "python")
                if os.path.exists(py):
                    fp = os.path.join(work, "freeze.txt")
                    open(fp, "w").write(sh([py, "-m", "pip", "freeze"], 90) + "\n")
                    tar.add(fp, top + "venvs" + os.path.abspath(v) + "/requirements.txt")
            for name, text in (("MANIFEST.txt", manifest(cfg, n) + venv_report(venvs) + skipped_report(skipped)), ("RESTORE.md", RESTORE),
                               ("requirements.txt", sh([sys.executable, "-m", "pip", "freeze"], 60) + "\n")):
                p = os.path.join(work, name)
                open(p, "w").write(text)
                tar.add(p, top + name)
        if os.path.getsize(plain) > st["max_archive_mb"] * 1e6:
            big = ", ".join(f"{os.path.basename(k)} {v / 1e6:.0f} MB" for k, v in sorted(sizes.items(), key=lambda kv: -kv[1])[:4])
            raise RuntimeError(f"backup would be {os.path.getsize(plain) / 1e6:.0f} MB (limit {st['max_archive_mb']:g}); biggest: {big}; "
                               "add them to backup.exclude_dirs")
        out = plain
        if st["encrypt_passphrase_file"]:
            if not os.path.isfile(st["encrypt_passphrase_file"]):
                raise RuntimeError("backup.encrypt_passphrase_file does not exist")
            out = plain + ".enc"
            final += ".enc"
            encrypt(plain, out, st["encrypt_passphrase_file"])
        os.chmod(out, 0o600)
        shutil.move(out, final)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    verify(final, st)
    rotate(st, final)
    return final


def archives(st):
    return sorted(glob.glob(os.path.join(st["dir"], PREFIX + "-*.tar.gz")) + glob.glob(os.path.join(st["dir"], PREFIX + "-*.tar.gz.enc")))


def rotate(st, newest=None):
    old = [p for p in archives(st) if p != newest]
    for p in old[:max(0, len(old) - (st["keep"] - 1))]:         # the new archive always stays
        os.remove(p)


def _open_plain(path, st, tmpdir):
    if path.endswith(".enc"):
        if not st["encrypt_passphrase_file"]:
            raise RuntimeError("encrypted archive and no backup.encrypt_passphrase_file")
        out = os.path.join(tmpdir, "plain.tar.gz")
        decrypt(path, out, st["encrypt_passphrase_file"])
        return out
    return path


def verify(path, st):
    """the archive must read back completely and the database inside must pass integrity_check; returns the member count"""
    with tempfile.TemporaryDirectory(dir=st["dir"]) as t:
        plain = _open_plain(path, st, t)
        with tarfile.open(plain) as tar:
            names = tar.getnames()
            for need in ("db/wsm.sqlite", "MANIFEST.txt", "RESTORE.md"):
                if PREFIX + "/" + need not in names:
                    raise RuntimeError(f"archive misses {need}")
            tar.extract(PREFIX + "/db/wsm.sqlite", t, **({"filter": "data"} if sys.version_info >= (3, 12) else {}))
        c = sqlite3.connect(os.path.join(t, PREFIX, "db", "wsm.sqlite"))
        try:
            if c.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError("database inside the archive is corrupt")
        finally:
            c.close()
    return len(names)


def restore(path, to, st):
    to = os.path.abspath(os.path.expanduser(to))
    os.makedirs(to, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=to) as t:
        plain = _open_plain(path, st, t)
        with tarfile.open(plain) as tar:
            for m in tar.getmembers():                              # refuse absolute paths and ..
                if m.name.startswith("/") or ".." in m.name.split("/") or not (m.isfile() or m.isdir()):
                    raise RuntimeError(f"unsafe member in the archive: {m.name}")
            tar.extractall(to)
    print(f"extracted to {os.path.join(to, PREFIX)}\n")
    print(open(os.path.join(to, PREFIX, "RESTORE.md")).read())


# ---------- scheduler ----------
def _state_path(cfg):
    return os.path.join(cfg["data_dir"], "backup.json")


def _load(cfg):
    try:
        return json.load(open(_state_path(cfg)))
    except Exception:
        return {}


def _save(cfg, d):
    tmp = _state_path(cfg) + ".tmp"
    json.dump(d, open(tmp, "w"))
    os.replace(tmp, _state_path(cfg))


def _notify(cfg, title, msg, prio, tag, log):
    try:
        import wsm_health
        h = wsm_health.settings(cfg)
        if h["ntfy_topic"]:
            wsm_health.ntfy(h, "WSM: " + title, msg, prio, (tag,))
    except Exception as e:
        log(f"backup: notification failed ({type(e).__name__})")


def run_once(cfg, log=print):
    """one backup + bookkeeping + notifications; returns True on success"""
    st, state = settings(cfg), _load(cfg)
    try:
        t0 = time.time()
        path = make_backup(cfg)
        state.update(last_ok=time.time(), last_file=os.path.basename(path), failing=False)
        log(f"backup: {os.path.basename(path)} ({os.path.getsize(path) / 1e6:.1f} MB, {time.time() - t0:.0f} s); keeping {st['keep']}")
        if state.pop("notified_fail", False) and st["notify"]:
            _notify(cfg, "backup works again", f"Last backup: {os.path.basename(path)}", 3, "white_check_mark", log)
        _save(cfg, state)
        return True
    except Exception as e:
        log(f"backup: FAILED ({type(e).__name__}: {e})")
        state["failing"] = True
        if st["notify"] and not state.get("notified_fail"):
            _notify(cfg, "backup failed", f"{type(e).__name__}: {e}"[:300] + "\nCheck: journalctl -u wsm | grep backup", 4, "warning", log)
            state["notified_fail"] = True
        _save(cfg, state)
        return False


def next_run(st, now=None):
    z = tz(st["timezone"])
    now = (now or dt.datetime.now(z)).astimezone(z)
    h, m = [int(x) for x in st["time"].split(":")[:2]]
    t = now.replace(hour=h, minute=m, second=0, microsecond=0)
    return t if t > now else t + dt.timedelta(days=1)


def run_forever(cfg, stop, log=print):
    st = settings(cfg)
    z = tz(st["timezone"])
    last = _load(cfg).get("last_ok")
    h, m = [int(x) for x in st["time"].split(":")[:2]]
    today_due = dt.datetime.now(z).replace(hour=h, minute=m, second=0, microsecond=0)
    missed = dt.datetime.now(z) >= today_due and (not last or dt.datetime.fromtimestamp(last, z) < today_due)
    log(f"backup: daily at {st['time']} {st['timezone']} into {st['dir']}" + (" (encrypted)" if st["encrypt_passphrase_file"] else "")
        + ("; today's backup is missing, running it now" if missed else ""))
    if missed and not stop.wait(90):
        run_once(cfg, log)
    while True:
        wait = (next_run(st) - dt.datetime.now(z)).total_seconds()
        if stop.wait(max(1, wait)):
            return
        run_once(cfg, log)
        if stop.wait(61):
            return


def start(cfg, stop, log=print):
    if not settings(cfg)["enabled"]:
        log("backup: off (backup.enabled: no)")
        return None
    th = threading.Thread(target=run_forever, args=(cfg, stop, log), name="backup", daemon=True)
    th.start()
    return th


def main():
    ap = argparse.ArgumentParser(description="WSM backup")
    ap.add_argument("-c", "--config", default=os.path.expanduser("~/wsm/wsm.yaml"))
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--now", action="store_true", help="make a backup now")
    g.add_argument("--list", action="store_true", help="list the backups")
    g.add_argument("--verify", metavar="FILE", help="check an archive (readable, database intact)")
    g.add_argument("--restore", metavar="FILE", help="unpack an archive into --to and print the restore guide")
    ap.add_argument("--to", default="~/restore")
    a = ap.parse_args()
    cfg = wsm_config.load(a.config if os.path.exists(os.path.expanduser(a.config)) else None)
    st = settings(cfg)
    if a.now:
        sys.exit(0 if run_once(cfg) else 1)
    if a.list:
        for p in archives(st):
            print(f"{os.path.basename(p)}  {os.path.getsize(p) / 1e6:6.1f} MB")
        s = _load(cfg)
        print(f"{len(archives(st))} backup(s) in {st['dir']}; last ok: " + (time.strftime("%Y-%m-%d %H:%M", time.localtime(s["last_ok"])) if s.get("last_ok") else "never"))
    elif a.verify:
        print(f"OK: {verify(os.path.expanduser(a.verify), st)} members, database intact")
    else:
        restore(os.path.expanduser(a.restore), a.to, st)


if __name__ == "__main__":
    main()
