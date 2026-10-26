"""Drone (platform) registry.

The website groups experiments by platform. A flight's platform comes from the ``platform:`` line of its
log header; logs without one (older formats) are matched by the start of their raw_data folder name.
Add an entry here when a new drone starts flying.
"""

from __future__ import annotations

PLATFORMS: dict[str, dict] = {
    "TAROT_450": {
        "label": "Tarot 450",
        "kind": "Quadcopter",
        "folder_prefixes": ["Tarot450"],
        "description": "Tarot 450 quadcopter with an ArduPilot flight controller and a Raspberry Pi logger.",
    },
    "AURELIA_X6": {
        "label": "Aurelia X6",
        "kind": "Hexacopter",
        "folder_prefixes": ["AureliaX6", "aurelia_x6"],
        "description": "Aurelia X6 hexacopter; autopilot telemetry and the onboard computer's sensors are recorded "
                       "as ROS 2 topics.",
    },
}


def platform_for(header_platform: str | None, folder_name: str, header_label: str | None = None) -> dict:
    """Return ``{"id", "label", "kind", "description"}`` for a flight."""
    pid = (header_platform or "").strip().upper() or None
    if pid is None:
        for key, p in PLATFORMS.items():
            if any(folder_name.lower().startswith(x.lower()) for x in p.get("folder_prefixes", [])):
                pid = key
                break
    if pid is None:
        pid = folder_name.split("_")[0].upper() or "UNKNOWN"
    p = PLATFORMS.get(pid, {})
    label = p.get("label") or (header_label or pid.replace("_", " ").title()).split(" (")[0]
    return {
        "id": pid,
        "label": label,
        "kind": p.get("kind", ""),
        "description": p.get("description", ""),
        "short": (p.get("folder_prefixes") or [pid.replace("_", "")])[0],  # used in flight IDs, e.g. Tarot450
    }
