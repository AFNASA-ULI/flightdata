"""Reader for the USAFA HWAS (High Wind Alert System) mesonet weather-station exports.

Layout (``raw_data/hwas_data/hwas_wx_data_<YYYYMMDD_HHMMSS>.csv``), one row every 30 s::

    Timestamp,Elapsed Seconds,Direction (deg),Speed (m/s),Gust (m/s),Temperature (C),Humidity (%),Pressure (Pa)
    09/29/2026 07:55:19,0.0,350,3.09,0.00,11.11,78,101456

* ``Timestamp`` is local Mountain Time (the file name carries the same local start time).
* Speeds come in whole-knot steps (0.514 m/s) and temperatures in whole-°F steps, i.e. the station reports in
  knots and °F and the export converts to SI.
* ``Direction`` is where the wind comes from (meteorological convention).
* ``Pressure`` is about 101.5 kPa at an elevation of ~2 km, so it is sea-level-adjusted, not station pressure.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from ..localtime import LOCAL_TZ

NAME = "hwas_csv"
DIR = "hwas_data"

COLUMNS = {
    "Direction (deg)": "wind_dir_deg",
    "Speed (m/s)": "wind_speed_m_s",
    "Gust (m/s)": "gust_m_s",
    "Temperature (C)": "temperature_c",
    "Humidity (%)": "humidity_pct",
    "Pressure (Pa)": "pressure_sealevel_pa",
}


def find(raw_root: Path) -> list[Path]:
    return sorted((raw_root / DIR).glob("*.csv"))


def read(path: Path) -> pd.DataFrame:
    raw = pd.read_csv(path)
    local = pd.to_datetime(raw["Timestamp"], format="%m/%d/%Y %H:%M:%S").dt.tz_localize(
        LOCAL_TZ, ambiguous="NaT", nonexistent="NaT")
    df = pd.DataFrame({"time_utc": local.dt.tz_convert("UTC")})
    for src, dst in COLUMNS.items():
        df[dst] = pd.to_numeric(raw.get(src), errors="coerce")
    df["source"] = path.name
    return df.dropna(subset=["time_utc"]).sort_values("time_utc").reset_index(drop=True)
