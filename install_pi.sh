#!/usr/bin/env bash
# install_pi.sh - install WSM on a Raspberry Pi (Raspberry Pi OS / Debian 12, 32 or 64 bit). Run as the normal user (pi), not root:
#     cd ~/wsm && ./install_pi.sh            # everything except starting the service
#     ./install_pi.sh --start                 # ... and enable + start wsm.service at the end
# Safe to run again: every step checks first and skips what is already done.
set -euo pipefail
WSM="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
START=no; [ "${1:-}" = "--start" ] && START=yes
say()  { printf '\n\033[1m== %s\033[0m\n' "$*"; }
die()  { printf '\n\033[31mERRO: %s\033[0m\n' "$*" >&2; exit 1; }
[ "$(id -u)" -ne 0 ] || die "rode como usuario normal (pi), sem sudo: o script chama sudo quando precisa"
[ "$WSM" = "$HOME/wsm" ] || die "a pasta deve ser $HOME/wsm (esta em $WSM)"

say "sistema"
echo "$(tr -d '\0' </proc/device-tree/model 2>/dev/null)  |  $(. /etc/os-release; echo "$PRETTY_NAME")  |  $(dpkg --print-architecture)"
free -m | awk '/Mem:/ {print "RAM total " $2 " MB, livre " $7 " MB"}'

say "pacotes (apt)"
PKGS="build-essential cmake git libfftw3-dev python3-numpy python3-scipy python3-yaml python3-soapysdr soapysdr-tools libsoapysdr-dev sqlite3"
MISSING=$(for p in $PKGS; do dpkg -s "$p" >/dev/null 2>&1 || echo "$p"; done)
if [ -n "$MISSING" ]; then
    sudo apt-get update
    sudo apt-get install -y $MISSING
else
    echo "todos instalados"
fi

say "API do SDRplay"
ls /usr/local/lib/libsdrplay_api.so* >/dev/null 2>&1 \
    || die "API do SDRplay nao encontrada. Baixe o instalador ARM (Linux, API 3.x) em sdrplay.com, rode 'sudo sh SDRplay_RSP_API-*.run' e rode este script de novo."
SDRPLAY=$(systemctl list-unit-files --type=service --no-legend 2>/dev/null | awk '{print $1}' | grep -i -m1 '^sdrplay' | sed 's/\.service$//' || true)
[ -n "$SDRPLAY" ] || die "servico do SDRplay nao encontrado no systemd (reinstale a API)"
sudo systemctl enable --now "$SDRPLAY.service"
echo "servico: $SDRPLAY.service ($(systemctl is-active "$SDRPLAY.service"))"

say "modulo SoapySDRPlay3"
if SoapySDRUtil --info 2>/dev/null | grep -q -i 'sdrplay'; then
    echo "ja instalado"
else
    B=$(mktemp -d)
    git clone --depth 1 https://github.com/pothosware/SoapySDRPlay3.git "$B/SoapySDRPlay3"
    cmake -S "$B/SoapySDRPlay3" -B "$B/build" -DCMAKE_BUILD_TYPE=Release
    make -C "$B/build" -j2                      # -j2: the Pi 3B has only 1 GB for 4 compilers
    sudo make -C "$B/build" install
    sudo ldconfig
    rm -rf "$B"
    SoapySDRUtil --info | grep -i sdrplay || die "o modulo foi compilado mas o SoapySDR nao o encontra"
fi

say "decodificador (wsm_decode)"
make -C "$WSM/decoder" clean >/dev/null
make -C "$WSM/decoder" -j2 >/tmp/wsm_make.txt 2>&1 || die "falha ao compilar o decodificador (veja /tmp/wsm_make.txt)"
[ -x "$WSM/decoder/wsm_decode" ] || die "falha ao compilar o decodificador (veja /tmp/wsm_make.txt)"
echo "compilado: $WSM/decoder/wsm_decode"

say "autoteste (sinais simulados, sem radio; leva 1-3 minutos no Pi 3B)"
mkdir -p "$WSM/data"
T0=$(date +%s)
(cd "$WSM" && WSM_TEST_PORT=8191 PYTHONPATH=. timeout 900 python3 wsm_selftest.py) >/tmp/wsm_selftest.txt 2>&1 \
    || die "o autoteste falhou (veja /tmp/wsm_selftest.txt)"
grep -E '^(20m|40m|30m|10m) |^total' /tmp/wsm_selftest.txt || true
grep -q 'PU2TKN' /tmp/wsm_selftest.txt || die "o autoteste nao decodificou o PU2TKN (veja /tmp/wsm_selftest.txt)"
echo "autoteste OK em $(( $(date +%s) - T0 )) s (8 slots)"

say "configuracao (wsm.yaml)"
if [ ! -f "$WSM/wsm.yaml" ]; then
    cp "$WSM/wsm.yaml.example" "$WSM/wsm.yaml"
    echo "criado a partir do exemplo: edite $WSM/wsm.yaml (ou copie o da VM por cima)"
fi
chmod 600 "$WSM/wsm.yaml"
if grep -q -E '^\s*extra_paths:.*sbm' "$WSM/wsm.yaml"; then
    echo "AVISO: wsm.yaml tem 'extra_paths' com o SBM (backups de ~200 MB). No Pi use:  extra_paths: []"
fi

say "servico systemd"
sed -e "s#@USER@#$USER#g" -e "s#@HOME@#$HOME#g" -e "s#@SDRPLAY@#$SDRPLAY#g" "$WSM/deploy/wsm-pi.service" \
    | sudo tee /etc/systemd/system/wsm.service >/dev/null
sudo systemctl daemon-reload
systemd-analyze verify /etc/systemd/system/wsm.service 2>&1 | grep -v -i 'sbm.service' || true
if [ "$START" = yes ]; then
    sudo systemctl enable --now wsm.service
    sleep 5
    systemctl --no-pager status wsm.service | head -12
else
    echo "instalado, nao iniciado. Para iniciar:  sudo systemctl enable --now wsm"
fi

say "pronto"
echo "Pagina: http://127.0.0.1:8091/  (no Pi)   |   log:  journalctl -u wsm -f"
