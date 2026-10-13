"""Turn one flight's raw data into cleaned, standard-schema tables.

Repository layout::

    raw_data/<flight>/<log>.csv          flight log(s), never edited
    raw_data/wind_drone/<bag>_csv/       wind-drone logs; each covers any number of flights
    processed/<flight>/                  written by nasauli.pipeline
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

from .localtime import series_to_local
from .platforms import platform_for
from .readers import hwas, reader_for, wind_ros2

RAW = "raw_data"
WIND_DIR = "wind_drone"
EARTH_RADIUS_M = 6_371_000.0
WIND_MARGIN_S = 5.0
HWAS_MARGIN_S = 60.0  # the station reports every 30 s; keep a sample or two either side of the flight
AIRBORNE_ALT_M = 1.0


@dataclass
class Session:
    source: Path
    flight: pd.DataFrame
    info: dict
    wind: pd.DataFrame | None = None
    wind_info: dict = field(default_factory=dict)
    hwas: pd.DataFrame | None = None
    hwas_info: dict = field(default_factory=dict)

    @property
    def stem(self) -> str:
        return self.source.stem

    @property
    def flight_id(self) -> str:
        return self.source.parent.name


def armed_or_unknown(df: pd.DataFrame) -> pd.Series:
    """Armed flag, or all-True when the log format has no armed information."""
    a = df["armed"]
    if a.isna().all():
        return pd.Series(True, index=df.index)
    return a.fillna(False).astype(bool)


def airborne(df: pd.DataFrame) -> pd.Series:
    return armed_or_unknown(df) & (df["alt_rel_m"] > AIRBORNE_ALT_M)


def add_local_position(df: pd.DataFrame) -> dict:
    """Add north_m / east_m relative to home (position at arming, else first valid fix)."""
    valid = df["lat_deg"].notna() & (df["lat_deg"].abs() > 0.1)
    armed_valid = valid & df["armed"].fillna(False).astype(bool)
    idx = armed_valid.idxmax() if armed_valid.any() else (valid.idxmax() if valid.any() else None)
    if idx is None:
        df["north_m"] = np.nan
        df["east_m"] = np.nan
        return {}
    lat0, lon0 = float(df.at[idx, "lat_deg"]), float(df.at[idx, "lon_deg"])
    lat = df["lat_deg"].where(valid)
    lon = df["lon_deg"].where(valid)
    df["north_m"] = (np.radians(lat - lat0) * EARTH_RADIUS_M).round(3)
    df["east_m"] = (np.radians(lon - lon0) * EARTH_RADIUS_M * np.cos(np.radians(lat0))).round(3)
    return {"lat_deg": round(lat0, 7), "lon_deg": round(lon0, 7)}


def trim_wind(wind: pd.DataFrame, flight: pd.DataFrame, margin_s: float = WIND_MARGIN_S):
    """Cut the wind log to the flight's time window, both on the GPS-corrected UTC clock.

    The wind-drone timestamps are taken as true UTC. The flight's host clock is shifted onto the
    autopilot's GPS time first (``time_gps_utc``), so a drifting logger clock does not shift the
    window.
    """
    t0 = flight["time_gps_utc"].min()
    t1 = flight["time_gps_utc"].max()
    lo, hi = t0 - pd.Timedelta(seconds=margin_s), t1 + pd.Timedelta(seconds=margin_s)
    cut = wind[(wind["time_utc"] >= lo) & (wind["time_utc"] <= hi)].copy()
    cut["elapsed_s"] = (
        (cut["time_utc"] - t0).dt.total_seconds() + float(flight["elapsed_s"].iloc[0])
    ).round(3)
    cut["time_local"] = series_to_local(cut["time_utc"])
    cut = cut[["time_utc", "sensor_stamp_utc", "time_local", "elapsed_s", "wind_speed_m_s", "wind_dir_deg",
               "wind_z", "temperature_c", "source"]].reset_index(drop=True)

    flight_s = (t1 - t0).total_seconds()
    if len(cut):
        ov0 = max(t0, cut["time_utc"].min())
        ov1 = min(t1, cut["time_utc"].max())
        covered = max(0.0, (ov1 - ov0).total_seconds())
    else:
        covered = 0.0
    info = {
        "flight_start_gps_utc": t0.isoformat(),
        "flight_end_gps_utc": t1.isoformat(),
        "margin_s": margin_s,
        "rows": int(len(cut)),
        "coverage_pct": round(100 * covered / flight_s, 1) if flight_s > 0 else 0.0,
        "sources": sorted(cut["source"].unique().tolist()),
    }
    return cut, info


def trim_hwas(station: pd.DataFrame, flight: pd.DataFrame, margin_s: float = HWAS_MARGIN_S):
    """HWAS weather-station rows around the flight (GPS time), on the flight's elapsed_s axis."""
    t0 = flight["time_gps_utc"].min()
    t1 = flight["time_gps_utc"].max()
    lo, hi = t0 - pd.Timedelta(seconds=margin_s), t1 + pd.Timedelta(seconds=margin_s)
    cut = station[(station["time_utc"] >= lo) & (station["time_utc"] <= hi)].copy()
    cut["time_local"] = series_to_local(cut["time_utc"])
    cut["elapsed_s"] = ((cut["time_utc"] - t0).dt.total_seconds() + float(flight["elapsed_s"].iloc[0])).round(3)
    cols = ["time_utc", "time_local", "elapsed_s", "wind_speed_m_s", "wind_dir_deg", "gust_m_s", "temperature_c",
            "humidity_pct", "pressure_sealevel_pa", "source"]
    cut = cut[cols].reset_index(drop=True)
    return cut, {"margin_s": margin_s, "rows": int(len(cut)), "sources": sorted(cut["source"].unique().tolist())}


@lru_cache(maxsize=None)
def load_hwas_logs(root: Path) -> tuple[pd.DataFrame | None, tuple[dict, ...]]:
    """All HWAS weather-station exports under raw_data/hwas_data/, plus a per-file summary."""
    parts, logs = [], []
    for f in hwas.find(root / RAW):
        h = hwas.read(f)
        if not len(h):
            continue
        parts.append(h)
        logs.append({"name": f.name, "start_utc": h["time_utc"].min().isoformat(),
                     "end_utc": h["time_utc"].max().isoformat(), "rows": int(len(h))})
    if not parts:
        return None, ()
    return pd.concat(parts).sort_values("time_utc").reset_index(drop=True), tuple(logs)


@lru_cache(maxsize=None)
def load_wind_logs(root: Path) -> tuple[pd.DataFrame | None, tuple[dict, ...]]:
    """All wind-drone logs under raw_data/wind_drone/, concatenated, plus a per-log summary."""
    parts, logs = [], []
    for d in wind_ros2.find(root / RAW / WIND_DIR):
        w = wind_ros2.read(d)
        w["source"] = d.name
        parts.append(w)
        logs.append({"name": d.name, "start_utc": w["time_utc"].min().isoformat(),
                     "end_utc": w["time_utc"].max().isoformat(), "rows": int(len(w)),
                     "bag_closed": wind_ros2.bag_closed(d)})
    if not parts:
        return None, ()
    return pd.concat(parts).sort_values("time_utc").reset_index(drop=True), tuple(logs)


def load_session(csv_path: Path, root: Path) -> Session | None:
    reader = reader_for(csv_path)
    if reader is None:
        return None
    df, info = reader.read(csv_path)
    df["time_local"] = series_to_local(df["time_gps_utc"])
    info["home"] = add_local_position(df)
    h = info["header"]
    info["platform"] = platform_for(h.get("platform"), csv_path.parent.name, h.get("platform_label"))
    sess = Session(source=csv_path, flight=df, info=info)

    wind, logs = load_wind_logs(root)
    if wind is not None and df["time_gps_utc"].notna().any():
        sess.wind, sess.wind_info = trim_wind(wind, df)
        sess.wind_info["reader"] = wind_ros2.NAME
        sess.wind_info["logs_available"] = list(logs)
    station, hlogs = load_hwas_logs(root)
    if station is not None and df["time_gps_utc"].notna().any():
        cut, hinfo = trim_hwas(station, df)
        if len(cut):
            sess.hwas, sess.hwas_info = cut, {**hinfo, "reader": hwas.NAME}
    return sess


def find_flight_dirs(root: Path) -> list[Path]:
    base = root / RAW
    skip = {WIND_DIR, hwas.DIR}
    return sorted(p for p in base.iterdir() if p.is_dir() and p.name not in skip) if base.is_dir() else []


def raw_logs(flight_dir: Path) -> list[Path]:
    """Flight logs in a raw_data/<flight> folder (hand-edited ``*_Fixed`` copies excluded)."""
    return sorted(p for p in flight_dir.glob("*.csv") if not p.stem.endswith("_Fixed"))
