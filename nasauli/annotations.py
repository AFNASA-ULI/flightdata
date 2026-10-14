"""Per-flight corrections that are not in the raw logs, keyed by flight ID (set by the team).

``keep_s``: (start, end) on the log's own ``elapsed_s`` axis; data outside it is dropped from the processed
outputs (None = no limit). The flight ID still comes from the start of the raw log, so links stay stable.
``desired_speeds_m_s`` overrides the header's ``commanded_speed_ms``; ``payload_g`` overrides the payload read
from the header's ``notes``.
"""

ANNOTATIONS: dict[str, dict] = {
    "Tarot450_20261009_095716": {"desired_speeds_m_s": [5, 8]},
    "Tarot450_20261009_100644": {"desired_speeds_m_s": [11, 14],
                                 "keep_s": (None, 280.0), "keep_reason": "not part of the test"},
    "Tarot450_20261009_101730": {"desired_speeds_m_s": [5, 8],
                                 "keep_s": (280.0, None), "keep_reason": "not part of the test"},
    "Tarot450_20261009_102747": {"desired_speeds_m_s": [11, 14], "payload_g": 360},
}


def for_flight(flight_id: str) -> dict:
    return ANNOTATIONS.get(flight_id, {})
