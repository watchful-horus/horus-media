#!/usr/bin/env bash
_CS_DEFAULT_URL="https://raw.githubusercontent.com/watchful-horus/horus-media/main"
_cs_boot="${COMMUNITY_SCRIPTS_CORE_DIR:-$(dirname "${BASH_SOURCE[0]}")/../../core}/core/build.func"
source "$_cs_boot" 2>/dev/null || source <(curl -fsSL "${COMMUNITY_SCRIPTS_CORE_URL:-https://raw.githubusercontent.com/community-scripts/core/main}/core/build.func")

# Copyright (c) 2026 The Horus authors
# License: MIT | https://github.com/watchful-horus/horus-media/raw/main/LICENSE
# Source: https://github.com/watchful-horus/horus-media

APP="Horus"
var_tags="${var_tags:-media;monitoring}"
var_cpu="${var_cpu:-1}"
var_ram="${var_ram:-512}"
var_disk="${var_disk:-4}"
var_os="${var_os:-debian}"
var_version="${var_version:-13}"
var_unprivileged="${var_unprivileged:-1}"
#var_arm64="${var_arm64:-yes}" # unset = ask the user; Horus is pure Python but has not been run on arm64 yet

export var_admin_user="${var_admin_user:-}"
export var_admin_pass="${var_admin_pass:-}"

header_info "$APP"
variables
color
catch_errors

function update_script() {
  header_info
  check_container_storage
  check_container_resources

  if [[ ! -d /opt/horus ]]; then
    msg_error "No Horus Installation Found!"
    exit
  fi

  if check_for_gh_release "horus" "watchful-horus/horus-media"; then
    msg_info "Stopping Service"
    systemctl stop horus
    msg_ok "Stopped Service"

    create_backup /opt/horus/data/horus.db

    CLEAN_INSTALL=1 CLEAN_INSTALL_KEEP="data" fetch_and_deploy_gh_release "horus" "watchful-horus/horus-media" "prebuild" "latest" "/opt/horus" "horus-media-*.tar.gz"

    restore_backup

    PYTHON_VERSION="3.12" setup_uv

    msg_info "Updating Horus"
    cd /opt/horus
    $STD uv venv --python 3.12 .venv
    $STD uv pip install --python .venv/bin/python /opt/horus/horus_media-*.whl
    msg_ok "Updated Horus"

    msg_info "Starting Service"
    systemctl start horus
    msg_ok "Started Service"
    msg_ok "Updated successfully!"
  fi
  exit
}

start
build_container
description

msg_ok "Completed Successfully!\n"
echo -e "${CREATING}${GN}${APP} setup has been successfully initialized!${CL}"
echo -e "${INFO}${YW}Access it using the following URL:${CL}"
echo -e "${GATEWAY}${BGN}http://${IP}:8000${CL}"
