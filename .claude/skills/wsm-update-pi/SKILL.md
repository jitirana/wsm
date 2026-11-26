---
name: wsm-update-pi
description: How to ship a WSM change to the Raspberry Pi - branch, self-test, the exact commands for the user (git pull, when to restart the service, when to rebuild the decoder), what the first minutes after a restart look like, and when to merge into main. Use whenever a change to the WSM code is ready to be tried on the Pi, or the user pastes the output of an update.
---

# Shipping a WSM change to the Pi

The user runs the commands on the Pi (`pi@wspr:~/wsm`) and pastes the output back. Reply in Brazilian Portuguese.

## 1. Before handing it over
- Work on a branch; commit with a clear English message; `git push -u origin <branch>`.
- `python3 wsm_selftest.py` must end with exit 0. Missing modules in a fresh container:
  `pip install numpy scipy pyyaml`, `apt-get install -y libfftw3-dev`, `make -C decoder`.
- If `wsm_web.py` changed, check the page script parses:
  ```bash
  python3 -c "import wsm_web,re;open('/tmp/p.js','w').write('\n'.join(re.findall(r'<script>(.*?)</script>',wsm_web.PAGE,re.S)))" && node --check /tmp/p.js
  ```
- Say what was tested here and what was not (e.g. "simulated data only", "wspr.live mocked").

## 2. The command for the user – pick by what changed
| Changed | Command |
|---|---|
| any `wsm_*.py` (service, web page, database, DSP) | `git pull` **and** `sudo systemctl restart wsm` |
| only `static/` (station.html, about.html) | `git pull` only, then Ctrl+F5 |
| only `tools/` or docs | `git pull` only (tools run by hand) |
| `decoder/` | `git pull && make -C decoder && sudo systemctl restart wsm` |
| `wsm.yaml` (user config) | edit, then `sudo systemctl restart wsm` |

First time on a branch:
```bash
cd ~/wsm && git fetch && git checkout <branch> && git pull && sudo systemctl restart wsm
```
Back to main after a merge:
```bash
cd ~/wsm && git checkout main && git pull && sudo systemctl restart wsm
```
Always tell the user to reload the page with **Ctrl+F5** (the browser caches the page script).

## 3. What a normal restart looks like (do not mistake it for a bug)
- The **waterfall panel disappears for 2–4 minutes**: waterfalls live in RAM and come back when the first slot ends.
- `[rsp2] error: RuntimeError: no available RSP devices found` right after a restart, followed about 10 s later by
  `[rsp2] radio open, 62500 S/s`, is normal: the old process was still releasing the RSP2. Only repeated errors with no
  `radio open` are a problem.
- New per-slot measurements (noise, spurs, thermal) only fill in from the slots after the restart; older days show "–".
- Check the log with:
  ```bash
  journalctl -u wsm -n 30 --no-pager | grep -iE "decoder|error|radio open|traceback"
  ```
  Expected: `decoder: WSJT-X wsprd` and `radio open, 62500 S/s`. If the decoder line says legacy, check that
  `decoder/wsprd_wsjtx` exists (`make -C decoder`).

## 4. Merge
Only when the user asks ("faz o merge"):
```bash
git fetch origin main <branch> && git checkout main && git merge --ff-only origin/main && git merge --no-ff --no-edit origin/<branch> && git push origin main
```
Then give the "back to main" command above, and update `docs/ROADMAP.md` if a roadmap item was finished.

## 5. Shutting the Pi down
`sudo shutdown -h now`, wait for the green LED to stop blinking (10–20 s), then unplug. Never pull the power while it
runs: the SQLite database and the SD card can be damaged.
