"""Reader for "nasauli session log v2.x" CSV files.

Layout: a block of ``# key: value`` lines ending with ``# ---``, then a normal CSV with a header row.

Columns are looked up by name and any that are missing come out as NaN, so small changes to the
logger's column set don't break the reader. The logger labels the magnetometer columns ``_mT``, but
the values are MAVLink SCALED_IMU milligauss; a ``_mG`` label is accepted too.
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pandas as pd

from ..schema import FLIGHT_COLUMNS

NAME = "session_v2"
MAGIC = "# nasauli session log v2."

G = 9.80665
DEG = 180.0 / math.pi

# canonical name: (candidate raw column names, scale factor)
COLUMN_MAP = {
    "sample_n": (["Sample_N"], 1),
    "elapsed_s": (["Elapsed_s"], 1),
    "flight_mode": (["MAV_FlightMode"], None),
    "mission_seq": (["MIS_Seq"], 1),
    "wp_dist_m": (["NAV_WPDist_m"], 1),
    "roll_deg": (["ATT_Roll_rad"], DEG),
    "pitch_deg": (["ATT_Pitch_rad"], DEG),
    "yaw_deg": (["ATT_Yaw_rad"], DEG),
    "roll_rate_deg_s": (["ATT_RollRate_rad_s"], DEG),
    "pitch_rate_deg_s": (["ATT_PitchRate_rad_s"], DEG),
    "yaw_rate_deg_s": (["ATT_YawRate_rad_s"], DEG),
    "lat_deg": (["POS_Lat_deg"], 1),
    "lon_deg": (["POS_Lon_deg"], 1),
    "alt_msl_m": (["POS_AltMSL_m"], 1),
    "alt_rel_m": (["POS_AltRel_m"], 1),
    "vel_n_m_s": (["POS_Vx_m_s"], 1),
    "vel_e_m_s": (["POS_Vy_m_s"], 1),
    "vel_d_m_s": (["POS_Vz_m_s"], 1),
    "heading_deg": (["POS_Heading_deg"], 1),
    "gps_lat_deg": (["GPS_Lat_deg"], 1),
    "gps_lon_deg": (["GPS_Lon_deg"], 1),
    "gps_alt_m": (["GPS_Alt_m"], 1),
    "gps_speed_m_s": (["GPS_Vel_m_s"], 1),
    "gps_course_deg": (["GPS_Course_deg"], 1),
    "gps_sats": (["GPS_Sats"], 1),
    "gps_fix_type": (["GPS_FixType"], 1),
    "gps_hdop": (["GPS_HDOP"], 1),
    "gps_vdop": (["GPS_VDOP"], 1),
    "groundspeed_m_s": (["VFR_Groundspeed_m_s"], 1),
    "airspeed_m_s": (["VFR_Airspeed_m_s"], 1),
    "climb_m_s": (["VFR_Climb_m_s"], 1),
    "throttle_pct": (["VFR_Throttle_pct"], 1),
    "acc_x_m_s2": (["IMU_Xacc_mg"], G / 1000),
    "acc_y_m_s2": (["IMU_Yacc_mg"], G / 1000),
    "acc_z_m_s2": (["IMU_Zacc_mg"], G / 1000),
    "gyro_x_rad_s": (["IMU_Xgyro_mrad_s"], 1 / 1000),
    "gyro_y_rad_s": (["IMU_Ygyro_mrad_s"], 1 / 1000),
    "gyro_z_rad_s": (["IMU_Zgyro_mrad_s"], 1 / 1000),
    # MAVLink SCALED_IMU magnetometer fields are milligauss; the logger mislabels them _mT.
    "mag_x_mgauss": (["IMU_Xmag_mG", "IMU_Xmag_mT"], 1),
    "mag_y_mgauss": (["IMU_Ymag_mG", "IMU_Ymag_mT"], 1),
    "mag_z_mgauss": (["IMU_Zmag_mG", "IMU_Zmag_mT"], 1),
    "batt_voltage_v": (["BAT_Voltage_V"], 1),
    "batt_current_a": (["BAT_Current_A"], 1),
    "batt_consumed_mah": (["BAT_Consumed_mAh"], 1),
    "batt_remaining_pct": (["BAT_Remaining_pct"], 1),
    "batt_temp_c": (["Battery_Temp_C"], 1),
    "motor1_us": (["SRV1_us"], 1),
    "motor2_us": (["SRV2_us"], 1),
    "motor3_us": (["SRV3_us"], 1),
    "motor4_us": (["SRV4_us"], 1),
    "vib_x_m_s2": (["VIB_X"], 1),
    "vib_y_m_s2": (["VIB_Y"], 1),
    "vib_z_m_s2": (["VIB_Z"], 1),
    "clip_0": (["VIB_Clip0"], 1),
    "clip_1": (["VIB_Clip1"], 1),
    "clip_2": (["VIB_Clip2"], 1),
    "ekf_flags": (["EKF_Flags"], 1),
    "ekf_vel_var": (["EKF_VelVariance"], 1),
    "ekf_pos_horiz_var": (["EKF_PosHorizVariance"], 1),
    "ekf_pos_vert_var": (["EKF_PosVertVariance"], 1),
    "ekf_compass_var": (["EKF_CompassVariance"], 1),
    "baro_press_hpa": (["BARO_Press_hPa"], 1),
    "baro_temp_c": (["BARO_Temp_degC"], 1),
    "ap_wind_dir_deg": (["WND_Direction_deg"], 1),
    "ap_wind_speed_m_s": (["WND_Speed_m_s"], 1),
    "ap_wind_speed_z_m_s": (["WND_SpeedZ_m_s"], 1),
}


def sniff(path: Path) -> bool:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.readline().startswith(MAGIC)
    except OSError:
        return False


def read_header(path: Path) -> tuple[dict, int]:
    """Return the ``# key: value`` preamble as a dict and the number of preamble lines."""
    header: dict[str, str] = {}
    n = 0
    with open(path, encoding="utf-8") as f:
        for line in f:
            if not line.startswith("#"):
                break
            n += 1
            # A spreadsheet round-trip pads every line with commas out to the column count.
            stripped = line.rstrip("\r\n")
            if stripped.rstrip(",") != stripped:
                header["_trailing_commas"] = "true"
            body = stripped.rstrip(",")[1:].strip()
            if body == "---":
                break
            if n == 1:
                header["format"] = body
                header["format_version"] = body.rsplit("v", 1)[-1]
                continue
            key, _, value = body.partition(":")
            header[key.strip()] = value.strip()
    return header, n


def _utc(s: pd.Series) -> pd.Series:
    return pd.to_datetime(s, utc=True, errors="coerce", format="ISO8601")


def read(path: Path) -> tuple[pd.DataFrame, dict]:
    """Read one v2 session log into the standard schema.

    Returns ``(df, info)`` where ``info`` holds the header, the columns that were used/missing, and
    anything the cleaning step needs that is not a per-sample value.
    """
    header, skip = read_header(path)
    raw = pd.read_csv(path, skiprows=skip, low_memory=False)

    out = pd.DataFrame(index=raw.index)
    out["time_utc"] = _utc(raw["Host_UTC"])
    used: set[str] = {"Host_UTC"}
    missing: list[str] = []
    for name, (candidates, scale) in COLUMN_MAP.items():
        src = next((c for c in candidates if c in raw.columns), None)
        if src is None:
            out[name] = np.nan
            missing.append(name)
            continue
        used.add(src)
        if scale is None:
            out[name] = raw[src].astype("string").replace("UNKNOWN", pd.NA)
        else:
            val = pd.to_numeric(raw[src], errors="coerce")
            # Unit conversion adds float noise; round converted values to well below sensor resolution.
            out[name] = val if scale == 1 else (val * scale).round(6)

    # Heartbeats from every MAVLink component on the link end up in HB_*; only the autopilot sets
    # MAV_MODE_FLAG_CUSTOM_MODE_ENABLED (bit 0). Armed is bit 7 of base_mode.
    # (A log recorded without a MAVLink connection has no HB_* columns at all.)
    base = pd.to_numeric(raw["HB_BaseMode"], errors="coerce") if "HB_BaseMode" in raw.columns \
        else pd.Series(np.nan, index=raw.index)
    autopilot = base.notna() & ((base.fillna(0).astype(int) & 1) == 1)
    armed = ((base.fillna(0).astype(int) & 128) != 0).where(autopilot)
    out["armed"] = armed.ffill().fillna(False).astype(bool)

    # Autopilot GPS-disciplined clock vs logger host clock.
    offset_s = np.nan
    if {"SYST_UnixUsec", "SYST_rx_UTC"} <= set(raw.columns):
        sys_t = pd.to_numeric(raw["SYST_UnixUsec"], errors="coerce") / 1e6
        rx = _utc(raw["SYST_rx_UTC"])
        ok = (sys_t > 1e9) & rx.notna()
        if ok.any():
            rx_s = (rx[ok] - pd.Timestamp(0, tz="UTC")).dt.total_seconds()
            offset_s = float(np.median(sys_t[ok] - rx_s))
    out["time_gps_utc"] = out["time_utc"] + pd.to_timedelta(
        0.0 if np.isnan(offset_s) else offset_s, unit="s"
    )

    # Keep every raw column that was not mapped, parsing timestamp columns.
    extras = {}
    for c in raw.columns:
        if c in used:
            continue
        if c.endswith("_UTC") and c != "RTC_UTC":
            extras[c] = _utc(raw[c])
        else:
            extras[c] = raw[c]
    out = pd.concat([out, pd.DataFrame(extras, index=raw.index)], axis=1)

    for c in FLIGHT_COLUMNS:  # filled in later by the cleaning step (e.g. north_m/east_m)
        if c not in out.columns:
            out[c] = np.nan
    out = out[list(FLIGHT_COLUMNS) + [c for c in out.columns if c not in FLIGHT_COLUMNS]]

    info = {
        "reader": NAME,
        "header": header,
        "gps_clock_offset_s": None if np.isnan(offset_s) else round(offset_s, 4),
        "missing_columns": missing,
        "raw_columns": list(raw.columns),
        "raw_hb_armed": raw.get("HB_Armed"),
        "raw_rtc_utc": raw.get("RTC_UTC"),
    }
    return out, info
