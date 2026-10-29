"""How to update Horus, worded for the way this copy is running.

Horus never updates itself: it only shows the steps. Docker and Proxmox containers update in
different ways, and a web app that replaced its own code (or controlled Docker) would be a bigger
risk than the convenience is worth.
"""

import shutil
from pathlib import Path

from . import listen

IMAGE = "ghcr.io/watchful-horus/horus-media"
BACK_UP_FIRST = "Back up first (Settings > General > Maintenance > Back up now). Your data is kept either way."


def kind() -> str:
    """"docker" (Docker or Kubernetes), "proxmox" (an LXC container) or "other"."""
    if listen.in_container():
        return "docker"
    try:
        if Path("/run/systemd/container").read_text().strip() == "lxc":
            return "proxmox"
    except OSError:
        pass
    return "other"


def update_help(latest: str, how: str | None = None) -> dict:
    """Steps for moving to version `latest`: a title, notes, and commands to copy."""
    how = how or kind()
    version = latest.lstrip("v")
    if how == "docker":
        return {
            "kind": how,
            "title": "Update the container",
            "notes": [
                BACK_UP_FIRST,
                (f"In the folder with your compose file, set the image to {IMAGE}:{version} "
                "(or :latest), then run:"),
            ],
            "commands": [{"label": "Docker Compose", "command": "docker compose pull && docker compose up -d"}],
            "after": [
                ("Without Compose: pull the new image, remove the old container and start it again "
                "with the same options and the same data volume."),
            ],
            "after_commands": [{"label": "Pull the image", "command": f"docker pull {IMAGE}:{version}"}],
        }
    if how == "proxmox":
        inside = next((c for c in ("update-horus", "update") if shutil.which(c)), None)
        if inside:
            return {
                "kind": how,
                "title": "Update this container",
                "notes": [BACK_UP_FIRST, "Open this container's Console in Proxmox and run:"],
                "commands": [{"label": "Inside the container", "command": inside}],
            }
        return {
            "kind": how,
            "title": "Update this container",
            "notes": [
                BACK_UP_FIRST,
                ("On the Proxmox host, run the install script's update with the new Horus package "
                f"(the horus_media-{version}-py3-none-any.whl from the release, or its web address):"),
            ],
            "commands": [{
                "label": "On the Proxmox host",
                "command": "HORUS_PACKAGE=<new wheel> bash horus-lxc.sh update <container id>",
            }],
        }
    return {
        "kind": "other",
        "title": "Update Horus",
        "notes": [
            BACK_UP_FIRST,
            (f"Download horus-media-v{version}.tar.gz from the release page, unpack it, install the "
            "wheel inside it into the same Python environment, then restart Horus:"),
        ],
        "commands": [{"label": "Install the wheel", "command": f"pip install --upgrade horus_media-{version}-py3-none-any.whl"}],
    }
