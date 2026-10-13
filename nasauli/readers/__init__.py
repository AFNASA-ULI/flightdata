"""Raw-format readers.

To support a new logger format, add a module here with ``NAME``, ``sniff(path) -> bool`` and
``read(path) -> (DataFrame in the standard schema, info dict)``, then list it in ``FLIGHT_READERS``.
Old readers stay, so older flights keep processing exactly as before.
"""

from __future__ import annotations

from pathlib import Path

from . import hwas, legacy_v1, session_v2, wind_ros2

FLIGHT_READERS = [session_v2, legacy_v1]


def reader_for(path: Path):
    """Return the reader module that understands ``path``, or None."""
    for r in FLIGHT_READERS:
        if r.sniff(path):
            return r
    return None


__all__ = ["FLIGHT_READERS", "reader_for", "hwas", "legacy_v1", "session_v2", "wind_ros2"]
