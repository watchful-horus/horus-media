#!/usr/bin/env bash

# Copyright (c) 2026 The Horus authors
# License: MIT | https://github.com/watchful-horus/horus-media/raw/main/LICENSE
# Source: https://github.com/watchful-horus/horus-media

source /dev/stdin <<<"$FUNCTIONS_FILE_PATH"
color
verb_ip6
catch_errors
setting_up_container
network_check
update_os

if [[ -z "${var_admin_user:-}" ]]; then
  var_admin_user=$(prompt_input "Horus admin username:" "admin" 60)
fi
var_admin_user="${var_admin_user:-admin}"
if [[ -z "${var_admin_pass:-}" ]]; then
  var_admin_pass=$(random_password 16)
  cat <<EOF >~/horus.creds
Horus admin login
Username: ${var_admin_user}
Password: ${var_admin_pass}
EOF
  chmod 600 ~/horus.creds
fi

PYTHON_VERSION="3.12" setup_uv
fetch_and_deploy_gh_release "horus" "watchful-horus/horus-media" "prebuild" "latest" "/opt/horus" "horus-media-*.tar.gz"

msg_info "Installing Horus"
cd /opt/horus
$STD uv venv --python 3.12 .venv
$STD uv pip install --python .venv/bin/python /opt/horus/horus_media-*.whl
printf '%s\n' "${var_admin_pass}" | HORUS_DB_PATH=/opt/horus/data/horus.db .venv/bin/python -m horus_media create-admin --username "${var_admin_user}"
# The port and network are saved in Horus itself (not in HORUS_PORT / HORUS_HOST, which would lock
# them), so they can be changed later on the Settings page.
HORUS_DB_PATH=/opt/horus/data/horus.db .venv/bin/python -m horus_media configure --port 8000 --listen-all
msg_ok "Installed Horus"

msg_info "Creating Service"
cat <<EOF >/etc/systemd/system/horus.service
[Unit]
Description=Horus
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/horus
Environment=HORUS_DB_PATH=/opt/horus/data/horus.db
ExecStart=/opt/horus/.venv/bin/python -m horus_media
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
systemctl enable -q --now horus
msg_ok "Created Service"

motd_ssh
customize
cleanup_lxc
