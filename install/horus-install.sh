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

PYTHON_VERSION="3.12" setup_uv
fetch_and_deploy_gh_release "horus" "watchful-horus/horus-media" "prebuild" "latest" "/opt/horus" "horus-media-*.tar.gz"

msg_info "Installing Horus"
cd /opt/horus
$STD uv venv --python 3.12 .venv
$STD uv pip install --python .venv/bin/python /opt/horus/horus_media-*.whl
# The first admin is created in the browser on the first visit (nothing is created here), so Horus
# must listen on the network before an admin exists: HORUS_OPEN_FIRST_RUN in the service below.
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
Environment=HORUS_OPEN_FIRST_RUN=1
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
