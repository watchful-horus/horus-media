#!/usr/bin/env bash
# Horus on Proxmox VE: a wizard that creates (or updates) a small container running Horus.
#
#   Run on the Proxmox host, as root:
#       bash horus-lxc.sh
#
#   It asks whether to install with default or advanced settings, or to update an existing Horus
#   container. Everything the wizard asks can also be given up front as environment variables, and
#   HORUS_WIZARD=0 skips the questions (for scripting):
#       HORUS_WIZARD=0 HORUS_PACKAGE=/root/horus_media-0.2.0-py3-none-any.whl bash horus-lxc.sh
#       HORUS_WIZARD=0 bash horus-lxc.sh update 120
#
# HORUS_PACKAGE is where Horus is installed from: a path to a wheel on this host, or a web address
# pip can download (a release wheel, or git+https://...). Build a wheel, with the web UI built
# first, using:  cd web && npm run build && cd .. && pip wheel . --no-deps -w dist
# When it is a web address, the container also gets an `update-horus` command that upgrades itself.
#
# Settings and their defaults:
#   CTID=<next free>  CT_NAME=horus  CORES=1  RAM=512 (MB)  DISK=4 (GB)  BRIDGE=<first bridge found>
#   IP=dhcp (or 192.168.1.50/24 with GATEWAY=192.168.1.1)  VLAN=  MTU=  MAC=  DNS=  SEARCHDOMAIN=
#   HORUS_PORT=8000  UNPRIVILEGED=1  TAGS=horus  ROOT_PASSWORD=  ADD_SSH_KEYS=no
#   ADMIN_USER=admin  ADMIN_PASSWORD=<8+ characters>  (creates the Horus login during the install;
#   without a password, a random one is generated and shown at the end)
#   STORAGE=<first storage that can hold containers>  TEMPLATE_STORAGE=<first that holds templates>
#   TZ=<this host's>
set -Eeuo pipefail

say() { printf '\033[1;32m==>\033[0m %s\n' "$*"; }
die() { printf '\033[1;31mError:\033[0m %s\n' "$*" >&2; exit 1; }
CREATED=""
trap 'rc=$?; [[ $rc -ne 0 ]] && echo >&2 "The script stopped at line $LINENO.${CREATED:+ The new container ($CREATED) was left as it is; remove it with: pct destroy $CREATED}"' ERR

# Long commands run as numbered steps: a spinner and one line each, with the full output going to a
# log file that is shown only when a step fails. HORUS_VERBOSE=1 prints everything instead.
VERBOSE="${HORUS_VERBOSE:-0}"
STEP=0; STEPS=0
LOG="$(umask 077; mktemp "${TMPDIR:-/tmp}/horus-install.XXXXXX")"

step() {  # step "Message" command [args...]
  local msg="$1" label rc=0 spinner
  shift
  STEP=$((STEP + 1))
  label="[${STEP}/${STEPS}] ${msg}"
  if (( VERBOSE )); then
    say "$label"
    "$@" || rc=$?
  elif [[ -t 1 ]]; then
    ( while :; do for f in '|' '/' '-' '\'; do printf '\r\033[K\033[1;34m%s\033[0m %s' "$f" "$label"; sleep 0.15; done; done ) 2>/dev/null &
    spinner=$!
    "$@" >>"$LOG" 2>&1 || rc=$?
    kill "$spinner" 2>/dev/null || true
    wait "$spinner" 2>/dev/null || true
    printf '\r\033[K'
  else
    "$@" >>"$LOG" 2>&1 || rc=$?
  fi
  if (( rc == 0 )); then
    (( VERBOSE )) || printf '\033[1;32m✔\033[0m %s\n' "$label"
  else
    printf '\033[1;31m✖\033[0m %s\n' "$label" >&2
    if (( ! VERBOSE )); then
      echo "The last lines of the log ($LOG):" >&2
      tail -n 25 "$LOG" >&2
    fi
    return "$rc"
  fi
}

command -v pct >/dev/null || die "This must run on a Proxmox VE host (pct not found)."
[[ $EUID -eq 0 ]] || die "Run as root."

# --- the wizard's building blocks: whiptail menus when there is a terminal, plain questions if not ---
INTERACTIVE=0
[[ -t 0 && -t 1 && "${HORUS_WIZARD:-1}" != "0" ]] && INTERACTIVE=1
HAVE_UI=0
(( INTERACTIVE )) && command -v whiptail >/dev/null && HAVE_UI=1

ask() {  # ask VAR "Question": shows the current value of VAR as the default
  local name="$1" prompt="$2" val cur
  cur="${!name:-}"  # separate line: the words of a single "local" are all expanded before any is assigned
  if (( HAVE_UI )); then
    val="$(whiptail --title "Horus" --inputbox "$prompt" 10 72 "$cur" 3>&1 1>&2 2>&3)" || exit 1
  else
    local nl='\n'  # the questions use \n for line breaks (whiptail understands it); plain mode doesn't
    read -r -p "${prompt//"$nl"/ } [$cur]: " val
  fi
  printf -v "$name" '%s' "${val:-$cur}"
}

choose() {  # choose "Question" tag1 "text1" tag2 "text2" ...  -> prints the chosen tag
  local prompt="$1"; shift
  if (( HAVE_UI )); then
    local def=()
    [[ -z "${CHOOSE_DEFAULT:-}" ]] || def=(--default-item "$CHOOSE_DEFAULT")
    whiptail --title "Horus" "${def[@]}" --menu "$prompt" 18 72 "$(( $# / 2 ))" "$@" 3>&1 1>&2 2>&3 || exit 1
  else
    local i=1 tags=() n
    echo "$prompt" >&2
    while (( $# )); do printf '  %d) %s\n' "$i" "$2" >&2; tags+=("$1"); shift 2; i=$((i + 1)); done
    read -r -p "Choose a number [1]: " n
    echo "${tags[$(( ${n:-1} - 1 ))]:?Not a choice}"
  fi
}

pick() {  # pick VAR "Question" tag text ...  -> menu; VAR's current value is pre-selected
  local var="$1" prompt="$2" chosen CHOOSE_DEFAULT
  shift 2
  CHOOSE_DEFAULT="${!var:-}"
  chosen="$(choose "$prompt" "$@")"
  printf -v "$var" '%s' "$chosen"
}

confirm() {  # confirm "Question" -> success if yes
  if (( HAVE_UI )); then
    whiptail --title "Horus" --yesno "$1" 18 72
  else
    local a; read -r -p "$1 [Y/n]: " a; [[ "${a:-y}" =~ ^[Yy] ]]
  fi
}

number() { [[ "$2" =~ ^[0-9]+$ ]] || die "$1 must be a number."; }

notify() {  # notify "message": shown in a box when there is a menu UI, else on stderr
  if (( HAVE_UI )); then whiptail --title "Horus" --msgbox "$1" 10 72; else echo "$1" >&2; fi
}

ask_valid() {  # ask_valid VAR "Question" check_function "Message when it fails": asks again until valid (blank is allowed)
  while :; do
    ask "$1" "$2"
    if [[ -z "${!1}" ]] || "$3" "${!1}"; then return 0; fi
    notify "$4"
  done
}

ask_secret() {  # ask_secret VAR "Question" [min length]: hidden input, asked twice; blank means none
  local var="$1" prompt="$2" min="${3:-5}" one two
  while :; do
    if (( HAVE_UI )); then
      one="$(whiptail --title "Horus" --passwordbox "$prompt" 10 72 3>&1 1>&2 2>&3)" || exit 1
      two="$(whiptail --title "Horus" --passwordbox "Type it again to confirm" 10 72 3>&1 1>&2 2>&3)" || exit 1
    else
      read -r -s -p "${prompt//$'\n'/ }: " one; echo
      read -r -s -p "Again: " two; echo
    fi
    if [[ "$one" != "$two" ]]; then notify "The two passwords do not match."
    elif [[ -n "$one" && ${#one} -lt $min ]]; then notify "Use at least $min characters, or leave it blank."
    else printf -v "$var" '%s' "$one"; return 0; fi
  done
}

first_storage() {  # first_storage <content type>
  pvesm status --content "$1" 2>/dev/null | awk 'NR > 1 && $3 == "active" {print $1; exit}'
}

# --- settings (environment overrides, then defaults) ---
PACKAGE="${HORUS_PACKAGE:-}"
PORT="${HORUS_PORT:-8000}"
CORES="${CORES:-1}"; RAM="${RAM:-512}"; DISK="${DISK:-4}"
BRIDGE="${BRIDGE:-}"; IP="${IP:-dhcp}"; GATEWAY="${GATEWAY:-}"; VLAN="${VLAN:-}"
MTU="${MTU:-}"; MAC="${MAC:-}"; DNS="${DNS:-}"; SEARCHDOMAIN="${SEARCHDOMAIN:-}"
UNPRIVILEGED="${UNPRIVILEGED:-1}"; TAGS="${TAGS:-horus}"; ROOT_PASSWORD="${ROOT_PASSWORD:-}"
ADD_SSH_KEYS="${ADD_SSH_KEYS:-no}"
ADMIN_USER="${ADMIN_USER:-admin}"; ADMIN_PASSWORD="${ADMIN_PASSWORD:-}"; ADMIN_CREATED=0; GENERATED_PASSWORD=""
CT_NAME="${CT_NAME:-horus}"
STORAGE="${STORAGE:-$(first_storage rootdir)}"
TEMPLATE_STORAGE="${TEMPLATE_STORAGE:-$(first_storage vztmpl)}"
host_timezone() {  # this host's zone: Debian 13 has no /etc/timezone, so ask systemd, then follow /etc/localtime
  local zone=""
  zone="$(timedatectl show -p Timezone --value 2>/dev/null || true)"
  [[ -n "$zone" ]] || zone="$(cat /etc/timezone 2>/dev/null || true)"
  [[ -n "$zone" ]] || zone="$(readlink /etc/localtime 2>/dev/null | sed 's|.*/zoneinfo/||' || true)"
  echo "$zone"
}
TIMEZONE="${TZ:-$(host_timezone)}"
CTID="${CTID:-}"

storage_items() {  # storage_items <content type> -> "name", "type, N GB free" lines for the menu
  pvesm status --content "$1" 2>/dev/null | awk 'NR > 1 && $3 == "active" {printf "%s\n%s, %.1f GB free\n", $1, $2, $6 / 1048576}'
}

bridge_items() {  # network bridges on this host -> "name", "address" lines for the menu
  # Only bridges that have an address on the host (the networks it is actually on). The firewall's
  # per-guest helper bridges (fwbr*) are never offered. A host whose bridges have no address of
  # its own, for example a VLAN-only bridge, still gets them listed rather than an empty menu.
  local d name addr with_addr="" without_addr=""
  for d in /sys/class/net/*/bridge; do
    [[ -d "$d" ]] || continue
    name="$(basename "$(dirname "$d")")"
    [[ "$name" == fwbr* ]] && continue
    addr="$(ip -4 -br addr show "$name" 2>/dev/null | awk '{print $3}')"
    if [[ -n "$addr" ]]; then with_addr+="$name"$'\n'"$addr"$'\n'
    else without_addr+="$name"$'\n'"no address on the host"$'\n'; fi
  done
  if [[ -n "$with_addr" ]]; then printf '%s' "$with_addr"; else printf '%s' "$without_addr"; fi
}

SSH_KEY_FILE=""
for _f in /root/.ssh/authorized_keys /etc/pve/priv/authorized_keys; do
  [[ -s "$_f" ]] && { SSH_KEY_FILE="$_f"; break; }
done

# --- checks used by both the wizard (to ask again) and the install (to stop early) ---
valid_ctid() { [[ "$1" =~ ^[0-9]+$ ]] && (( $1 >= 100 )) && pvesh get /cluster/nextid --vmid "$1" >/dev/null 2>&1; }
valid_hostname() { [[ "$1" =~ ^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$ ]]; }
valid_ipv4() {
  local IFS=. o; local -a p
  read -ra p <<<"$1"
  [[ "$1" =~ ^[0-9]+(\.[0-9]+){3}$ && ${#p[@]} -eq 4 ]] || return 1
  for o in "${p[@]}"; do (( 10#$o <= 255 )) || return 1; done
}
valid_cidr() {
  [[ "$1" =~ ^([0-9.]+)/([0-9]+)$ ]] || return 1
  local addr="${BASH_REMATCH[1]}" prefix="${BASH_REMATCH[2]}"  # valid_ipv4 runs its own regex, so copy these first
  valid_ipv4 "$addr" && (( 10#$prefix <= 32 ))
}
ip_int() { local IFS=. a b c d; read -r a b c d <<<"$1"; echo $(( (10#$a << 24) | (10#$b << 16) | (10#$c << 8) | 10#$d )); }
same_subnet() {  # same_subnet 192.168.1.50/24 192.168.1.1
  local prefix="${1#*/}" mask
  mask=$(( (0xFFFFFFFF << (32 - 10#$prefix)) & 0xFFFFFFFF ))
  (( ($(ip_int "${1%/*}") & mask) == ($(ip_int "$2") & mask) ))
}
valid_vlan() { [[ "$1" =~ ^[0-9]+$ ]] && (( $1 >= 1 && $1 <= 4094 )); }
valid_mtu() { [[ "$1" =~ ^[0-9]+$ ]] && (( $1 >= 576 && $1 <= 65535 )); }
valid_mac() { [[ "$1" =~ ^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$ ]]; }
valid_tags() { [[ "$1" =~ ^[A-Za-z0-9_.+-]+(\;[A-Za-z0-9_.+-]+)*$ ]]; }
valid_zone() { [[ -f "/usr/share/zoneinfo/$1" ]]; }
valid_bridge() { [[ -d "/sys/class/net/$1/bridge" ]]; }

valid_username() { [[ "$1" =~ ^[A-Za-z0-9][A-Za-z0-9._@+-]{2,63}$ ]]; }  # the same rule as Horus: an email address works

ask_admin() {  # the first Horus account, made during the install so the first-run screen never appears
  say "Horus administrator account"
  ask_valid ADMIN_USER "Username for the Horus administrator (an email address, or 3 to 64 letters, numbers, . - _ @ +)" valid_username "Use 3 to 64 letters, numbers, dots, dashes, underscores, @ or +."
  ask_secret ADMIN_PASSWORD "Password for $ADMIN_USER (at least 8 characters; blank: generate one and show it at the end)" 8
}

valid_package() { [[ -f "$1" || "$1" =~ ^(https?|git\+https)://[^[:space:]]+$ ]]; }

ask_package() {
  until valid_package "$PACKAGE"; do
    [[ -n "$PACKAGE" ]] && echo "That is not an existing file or a web address." >&2
    (( INTERACTIVE )) || die "Set HORUS_PACKAGE (see the top of this script)."
    PACKAGE=""
    ask PACKAGE "Where should Horus be installed from?\n\nA path to a Horus wheel (.whl) on this host, or a web address."
  done
}

install_in_container() {  # $1 = CTID; installs/upgrades Horus from $PACKAGE
  local ctid="$1" target="$PACKAGE"
  if [[ -f "$PACKAGE" ]]; then
    target="/tmp/$(basename "$PACKAGE")"
    pct push "$ctid" "$PACKAGE" "$target"
  fi
  # --force-reinstall: a rebuilt wheel keeps its version number, and pip would skip it otherwise
  pct exec "$ctid" -- /opt/horus/bin/pip install --upgrade --force-reinstall --no-cache-dir "$target"
  # Horus runs as the `horus` user; owning its own environment lets Settings > Update now work
  pct exec "$ctid" -- chown -R horus /opt/horus
}

horus_containers() {  # prints "id name" for containers this script created
  local id
  for id in $(pct list | awk 'NR > 1 {print $1}'); do
    pct config "$id" | grep -qi 'description:.*Horus' && echo "$id $(pct config "$id" | awk '/^hostname:/ {print $2}')"
  done
  return 0
}

do_update() {
  local ctid="${1:-}"
  if [[ -z "$ctid" ]]; then
    (( INTERACTIVE )) || die "Usage: horus-lxc.sh update <container id>"
    local items=() id name
    while read -r id name; do items+=("$id" "${name:-container}"); done < <(horus_containers)
    (( ${#items[@]} )) || die "No Horus container found on this host."
    ctid="$(choose "Which Horus container should be updated?" "${items[@]}")"
  fi
  ask_package
  say "Updating Horus in container $ctid (the data in /var/lib/horus is kept)"
  STEPS=3
  step "Stopping Horus" pct exec "$ctid" -- systemctl stop horus
  step "Updating Horus" install_in_container "$ctid"
  migrate_listen_env "$ctid"
  step "Starting Horus" pct exec "$ctid" -- systemctl start horus
  say "Done."
}

detect_network() {  # a bridge that exists on this host, unless one was given
  [[ -n "$BRIDGE" ]] && return 0
  local -a items
  mapfile -t items < <(bridge_items)
  BRIDGE="${items[0]:-vmbr0}"
}

valid_num() { [[ "$1" =~ ^[0-9]+$ ]] && (( $1 > 0 )); }
valid_port() { [[ "$1" =~ ^[0-9]+$ ]] && (( $1 >= 1 && $1 <= 65535 )); }

wizard_settings() {
  say "Advanced settings"
  local items=()
  pick UNPRIVILEGED "Which kind of container?" 1 "Unprivileged (recommended)" 0 "Privileged"
  ask_secret ROOT_PASSWORD "Root password for the container (blank: none; open a shell from this host with: pct enter <id>)"
  [[ -n "$CTID" ]] || CTID="$(pvesh get /cluster/nextid)"
  while :; do
    ask CTID "Container ID"
    valid_ctid "$CTID" && break
    notify "ID $CTID is already used, or is not a number of 100 or more."
  done
  ask_valid CT_NAME "Container name" valid_hostname "Use lowercase letters, numbers and dashes only (no spaces or dots)."
  ask_valid DISK "Disk size (GB)" valid_num "That must be a whole number above 0."
  ask_valid CORES "CPU cores" valid_num "That must be a whole number above 0."
  ask_valid RAM "Memory (MB)" valid_num "That must be a whole number above 0."

  mapfile -t items < <(storage_items rootdir)
  if (( ${#items[@]} > 2 )); then pick STORAGE "Which storage should hold the container's disk?" "${items[@]}"
  elif (( ${#items[@]} )); then STORAGE="${items[0]}"; say "Using the only storage that can hold containers: $STORAGE"
  else ask STORAGE "Storage for the container's disk"; fi
  mapfile -t items < <(storage_items vztmpl)
  if (( ${#items[@]} > 2 )); then pick TEMPLATE_STORAGE "Which storage should the Debian template be kept on?" "${items[@]}"; fi

  mapfile -t items < <(bridge_items)
  if (( ${#items[@]} > 2 )); then pick BRIDGE "Which network bridge should the container use?" "${items[@]}"
  elif (( ${#items[@]} )); then BRIDGE="${items[0]}"; say "Using the only network bridge: $BRIDGE"
  else ask BRIDGE "Network bridge"; fi

  if [[ "$IP" == "dhcp" ]]; then IP_MODE=dhcp; else IP_MODE=static; fi
  pick IP_MODE "How should the container get its IP address?" dhcp "Automatically (DHCP)" static "A fixed address"
  if [[ "$IP_MODE" == "dhcp" ]]; then
    IP=dhcp; GATEWAY=""
  else
    [[ "$IP" != "dhcp" ]] || IP=""
    while :; do
      ask_valid IP "IP address with its prefix, for example 192.168.1.50/24" valid_cidr "That is not an address like 192.168.1.50/24."
      ask_valid GATEWAY "Gateway, for example 192.168.1.1" valid_ipv4 "That is not an address like 192.168.1.1."
      [[ -n "$IP" && -n "$GATEWAY" ]] || { notify "A fixed address needs both the address and the gateway."; continue; }
      same_subnet "$IP" "$GATEWAY" && break
      notify "The gateway $GATEWAY is not in the same network as $IP."
    done
  fi
  ask_valid DNS "DNS server (blank: use the host's)" valid_ipv4 "That is not an address like 192.168.1.1."
  ask SEARCHDOMAIN "DNS search domain (blank: use the host's)"
  ask_valid VLAN "VLAN tag (blank for none)" valid_vlan "A VLAN tag is a number from 1 to 4094."
  ask_valid MTU "MTU (blank: default)" valid_mtu "The MTU must be a number from 576 to 65535."
  ask_valid MAC "MAC address (blank: generated), for example 02:AA:BB:CC:DD:EE" valid_mac "That is not a MAC address like 02:AA:BB:CC:DD:EE."

  ask_valid TAGS "Tags shown in the Proxmox list (separate with ;)" valid_tags "Use letters, numbers, - _ . and ; between tags."
  if [[ -n "$SSH_KEY_FILE" ]]; then
    pick ADD_SSH_KEYS "Let the SSH keys this host accepts ($SSH_KEY_FILE) log in to the container as root?" no "No" yes "Yes"
  fi
  ask_valid PORT "Port Horus listens on" valid_port "A port is a number from 1 to 65535."
  ask_valid TIMEZONE "Time zone, for example Europe/London (decides which day and hour plays count toward)" valid_zone "That is not a time zone name from /usr/share/zoneinfo."
}

summary() {
  cat <<EOF
Create a Horus container with:

  Container ID:   ${CTID:-next free}
  Name:           $CT_NAME   ($([[ "$UNPRIVILEGED" == 1 ]] && echo unprivileged || echo privileged))
  CPU / memory:   $CORES core(s), $RAM MB
  Disk:           $DISK GB on ${STORAGE:-?}
  Network:        $BRIDGE, ${IP}${GATEWAY:+ via $GATEWAY}${VLAN:+, VLAN $VLAN}${MTU:+, MTU $MTU}
  DNS:            ${DNS:-from the host}${SEARCHDOMAIN:+, search $SEARCHDOMAIN}
  Port:           $PORT
  Time zone:      ${TIMEZONE:-container default}
  Root password:  $([[ -n "$ROOT_PASSWORD" ]] && echo set || echo none)
  SSH keys:       $([[ "$ADD_SSH_KEYS" == yes ]] && echo "from $SSH_KEY_FILE" || echo none)
  Horus login:    administrator $ADMIN_USER, $([[ -n "$GENERATED_PASSWORD" ]] && echo "password generated for you" || echo "password as chosen")
  Installed from: $PACKAGE
EOF
}

check_settings() {  # stops with a clear message before anything is created
  [[ -n "$STORAGE" ]] || die "No storage that can hold containers was found. Set STORAGE."
  [[ -n "$TEMPLATE_STORAGE" ]] || die "No storage that can hold templates was found. Set TEMPLATE_STORAGE."
  valid_port "$PORT" || die "Port must be a number from 1 to 65535."
  valid_num "$CORES" || die "CPU cores must be a whole number above 0."
  valid_num "$RAM" || die "Memory must be a whole number above 0."
  valid_num "$DISK" || die "Disk size must be a whole number above 0."
  valid_hostname "$CT_NAME" || die "The container name may only hold lowercase letters, numbers and dashes."
  valid_bridge "$BRIDGE" || die "Network bridge $BRIDGE does not exist on this host."
  if [[ "$IP" != "dhcp" ]]; then
    valid_cidr "$IP" || die "IP must be dhcp or an address like 192.168.1.50/24."
    valid_ipv4 "$GATEWAY" || die "A fixed IP needs a gateway like 192.168.1.1."
    same_subnet "$IP" "$GATEWAY" || die "The gateway $GATEWAY is not in the same network as $IP."
  fi
  [[ -z "$DNS" ]] || valid_ipv4 "$DNS" || die "DNS must be an address like 192.168.1.1."
  [[ -z "$VLAN" ]] || valid_vlan "$VLAN" || die "A VLAN tag is a number from 1 to 4094."
  [[ -z "$MTU" ]] || valid_mtu "$MTU" || die "The MTU must be a number from 576 to 65535."
  [[ -z "$MAC" ]] || valid_mac "$MAC" || die "That is not a MAC address like 02:AA:BB:CC:DD:EE."
  valid_tags "$TAGS" || die "Tags may hold letters, numbers, - _ . and ; between tags."
  [[ -z "$TIMEZONE" ]] || valid_zone "$TIMEZONE" || die "$TIMEZONE is not a time zone name from /usr/share/zoneinfo."
  [[ "$UNPRIVILEGED" == 0 || "$UNPRIVILEGED" == 1 ]] || die "UNPRIVILEGED must be 1 or 0."
  [[ "$ADD_SSH_KEYS" != yes || -n "$SSH_KEY_FILE" ]] || die "No SSH keys were found on this host to add."
  valid_username "$ADMIN_USER" || die "The Horus username must be 3 to 64 letters, numbers, dots, dashes, underscores, @ or +."
  if [[ -z "$ADMIN_PASSWORD" ]]; then  # nothing chosen: make one, so nobody else can claim the first account
    ADMIN_PASSWORD="$(openssl rand -base64 33 | tr -dc 'A-Za-z0-9' | head -c 16 || true)"
    GENERATED_PASSWORD="$ADMIN_PASSWORD"
  fi
  (( ${#ADMIN_PASSWORD} >= 8 )) || die "The Horus password must be at least 8 characters."
}

find_template() {  # sets TEMPLATE to the newest Debian 13 template for this CPU type, downloading it if needed
  pveam update
  TEMPLATE="$(pveam available --section system | awk -v a="$ARCH" '$2 ~ "debian-13-standard.*_" a "[.]tar" {print $2}' | sort -V | tail -1)"
  [[ -n "$TEMPLATE" ]] || { echo "No Debian 13 template is available from Proxmox. Update Proxmox, or use the Docker image instead." >&2; return 1; }
  pveam list "$TEMPLATE_STORAGE" | grep -q "$TEMPLATE" || pveam download "$TEMPLATE_STORAGE" "$TEMPLATE"
}

create_container() {
  pct create "$CTID" "${TEMPLATE_STORAGE}:vztmpl/${TEMPLATE}" \
    --hostname "$CT_NAME" --unprivileged "$UNPRIVILEGED" --features nesting=1 --onboot 1 \
    --cores "$CORES" --memory "$RAM" --swap 256 \
    --rootfs "${STORAGE}:${DISK}" --net0 "$NET" --tags "$TAGS" "${EXTRA[@]}" \
    --description "Horus: dashboard for Plex and Emby"
  CREATED="$CTID"
  pct start "$CTID"
  if [[ -n "$ROOT_PASSWORD" ]]; then  # through stdin, so it never shows in a process list
    printf 'root:%s\n' "$ROOT_PASSWORD" | pct exec "$CTID" -- chpasswd
    ROOT_PASSWORD=""
  fi
}

wait_for_network() {
  local _
  for _ in $(seq 1 30); do
    pct exec "$CTID" -- getent hosts deb.debian.org >/dev/null 2>&1 && return 0
    sleep 2
  done
  echo "The container has no network or DNS." >&2
  return 1
}

install_python() {
  pct exec "$CTID" -- bash -c "export DEBIAN_FRONTEND=noninteractive LC_ALL=C.UTF-8; apt-get update -qq && apt-get install -y -qq python3 python3-venv ca-certificates tzdata"
  if [[ -n "$TIMEZONE" && -e "/usr/share/zoneinfo/$TIMEZONE" ]]; then
    pct exec "$CTID" -- bash -c "ln -sf /usr/share/zoneinfo/$TIMEZONE /etc/localtime && echo $TIMEZONE > /etc/timezone"
  fi
}

install_horus() {
  pct exec "$CTID" -- bash -c "python3 -m venv /opt/horus && useradd --system --home-dir /var/lib/horus --create-home --shell /usr/sbin/nologin horus"
  install_in_container "$CTID"
}

write_service() {
  pct exec "$CTID" -- bash -c "cat > /etc/systemd/system/horus.service" <<EOF
[Unit]
Description=Horus (dashboard for Plex and Emby)
After=network-online.target
Wants=network-online.target

[Service]
User=horus
Environment=HORUS_DB_PATH=/var/lib/horus/horus.db
ExecStart=/opt/horus/bin/python -m horus_media
Restart=on-failure
RestartSec=5
NoNewPrivileges=true
ProtectSystem=full
PrivateTmp=true

[Install]
WantedBy=multi-user.target
EOF
  if [[ "$PACKAGE" =~ ^(https?|git\+https):// ]]; then
    # Inside the container, `update-horus` upgrades Horus from the same address.
    pct exec "$CTID" -- bash -c "cat > /usr/local/bin/update-horus && chmod +x /usr/local/bin/update-horus" <<EOF
#!/bin/sh
set -e
systemctl stop horus
/opt/horus/bin/pip install --upgrade --force-reinstall --no-cache-dir '${PACKAGE}'
chown -R horus /opt/horus
systemctl start horus
echo "Horus updated."
EOF
  fi
  pct exec "$CTID" -- systemctl daemon-reload
  # Port and network are saved in Horus (not in HORUS_PORT / HORUS_HOST, which would lock them),
  # so they can be changed later on the Settings page.
  save_listen "$CTID" "$PORT"
}

save_listen() {  # $1 = CTID, $2 = port; other computers may connect (an admin login is required)
  pct exec "$1" -- runuser -u horus -- \
    env HORUS_DB_PATH=/var/lib/horus/horus.db /opt/horus/bin/python -m horus_media configure --port "$2" --listen-all
}

migrate_listen_env() {  # $1 = CTID; older installs fixed the port in the service file
  local ctid="$1" unit=/etc/systemd/system/horus.service port
  port="$(pct exec "$ctid" -- sed -n 's/^Environment=HORUS_PORT=//p' "$unit" | tr -d '\r' | head -n1)"
  [[ -n "$port" ]] || return 0
  save_listen "$ctid" "$port"
  pct exec "$ctid" -- sed -i '/^Environment=HORUS_\(PORT\|HOST\)=/d' "$unit"
  pct exec "$ctid" -- systemctl daemon-reload
}

create_admin() {
  # Before the service starts, so there is never a first-run screen open to the network. The
  # password goes in through stdin: it never appears in a command line or a log.
  printf '%s\n' "$ADMIN_PASSWORD" | pct exec "$CTID" -- runuser -u horus -- \
    env HORUS_DB_PATH=/var/lib/horus/horus.db /opt/horus/bin/python -m horus_media create-admin --username "$ADMIN_USER"
}

do_install() {
  say "Proxmox VE $(pveversion 2>/dev/null | sed 's|^pve-manager/||; s|/.*||'), $(uname -m)"
  case "$(uname -m)" in
    x86_64) ARCH=amd64 ;;
    aarch64) ARCH=arm64 ;;
    *) die "This host's CPU type ($(uname -m)) is not supported; Debian container templates exist for amd64 and arm64." ;;
  esac
  check_settings
  if [[ -n "$CTID" ]]; then
    valid_ctid "$CTID" || die "ID $CTID is already in use."
  else
    CTID="$(pvesh get /cluster/nextid)"
  fi
  if (( INTERACTIVE )); then confirm "$(summary)

Go ahead?" || exit 1; fi

  NET="name=eth0,bridge=${BRIDGE}"
  if [[ "$IP" == "dhcp" ]]; then NET+=",ip=dhcp"; else NET+=",ip=${IP},gw=${GATEWAY}"; fi
  [[ -z "$VLAN" ]] || NET+=",tag=${VLAN}"
  [[ -z "$MTU" ]] || NET+=",mtu=${MTU}"
  [[ -z "$MAC" ]] || NET+=",hwaddr=${MAC}"
  EXTRA=()
  [[ -z "$DNS" ]] || EXTRA+=(--nameserver "$DNS")
  [[ -z "$SEARCHDOMAIN" ]] || EXTRA+=(--searchdomain "$SEARCHDOMAIN")
  [[ "$ADD_SSH_KEYS" != yes ]] || EXTRA+=(--ssh-public-keys "$SSH_KEY_FILE")

  STEPS=7
  [[ -z "$ADMIN_PASSWORD" ]] || STEPS=8
  step "Finding the Debian 13 template" find_template
  step "Creating container $CTID ($CT_NAME)" create_container
  step "Waiting for the network" wait_for_network
  step "Installing Python" install_python
  step "Installing Horus" install_horus
  step "Creating the service" write_service
  if [[ -n "$ADMIN_PASSWORD" ]]; then
    step "Creating the administrator account ($ADMIN_USER)" create_admin
    ADMIN_CREATED=1
    ADMIN_PASSWORD=""
  fi
  step "Starting Horus" pct exec "$CTID" -- systemctl enable --now horus

  local addr="" _
  for _ in $(seq 1 20); do
    addr="$(pct exec "$CTID" -- hostname -I 2>/dev/null | awk '{print $1}')"
    [[ -n "$addr" ]] && break
    sleep 1
  done

  say "Horus is running."
  echo "    Open:         http://${addr:-<container address>}:${PORT}"
  if [[ -n "$GENERATED_PASSWORD" ]]; then
    echo "    Sign in:      ${ADMIN_USER} / ${GENERATED_PASSWORD}   (generated: save it, and change it under Settings)"
  else
    echo "    Sign in:      as ${ADMIN_USER} with the password you chose."
  fi
  echo "    Backups:      to bring over an old install, sign in and use Settings > Backups > upload."
  echo "    Full log:     ${LOG}"
  if [[ "$PACKAGE" =~ ^(https?|git\+https):// ]]; then
    echo "    Update:       run update-horus inside the container, or: bash $0 update ${CTID}"
  else
    echo "    Update:       HORUS_PACKAGE=<new wheel> bash $0 update ${CTID}"
  fi
  echo "    Data:         /var/lib/horus in container ${CTID} (back it up from Settings > Backups)"
}

# --- main ---
ACTION="${1:-}"
if [[ "$ACTION" == "update" ]]; then
  do_update "${2:-}"
  exit 0
fi

detect_network
if (( INTERACTIVE )); then
  ACTION="$(choose "What would you like to do?" \
    default "Install Horus with default settings" \
    advanced "Install Horus with advanced settings" \
    update "Update an existing Horus container" \
    quit "Quit")"
  case "$ACTION" in
    quit) exit 0 ;;
    update) do_update; exit 0 ;;
    advanced) ask_package; wizard_settings; ask_admin ;;
    *) ask_package; ask_admin ;;
  esac
else
  ask_package
fi
do_install
