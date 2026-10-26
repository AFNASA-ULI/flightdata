"""Raw-format readers.

To support a new logger format, add a module here with ``NAME``, ``sniff(path) -> bool`` and
``read(path) -> (DataFrame in the standard schema, info dict)``, then list it in ``FLIGHT_READERS``.
Old readers stay, so older flights keep processing exactly as before.
"""

from __future__ import annotations

from pathlib import Path

from . import ardupilot_csv, hwas, legacy_v1, ros2_telemetry, session_v2, wind_ros2

FLIGHT_READERS = [session_v2, legacy_v1, ardupilot_csv, ros2_telemetry]


def reader_for(path: Path):
    """Return the reader module that understands ``path``, or None."""
    for r in FLIGHT_READERS:
        if r.sniff(path):
            return r
    return None


def read_sessions(reader, path: Path):
    """[(df, info), ...]: one entry per flight. Most formats hold one flight per file."""
    if hasattr(reader, "read_sessions"):
        return reader.read_sessions(path)
    return [reader.read(path)]


__all__ = ["FLIGHT_READERS", "reader_for", "read_sessions", "ardupilot_csv", "hwas", "legacy_v1", "ros2_telemetry", "session_v2",
           "wind_ros2"]
