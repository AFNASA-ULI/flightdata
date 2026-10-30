"""Reader for ArduPilot onboard (DataFlash) logs exported as one CSV per message type.

This is the format Mission Planner writes when a .BIN log is converted to CSV, kept as received: a zip (or a
folder) holding ``ATT.csv``, ``POS.csv``, ``GPS_0.csv``, ``BAT_0.csv``, ... each with ``LineNo,TimeUS,...``.
Used for flights where the Pi logger had no MAVLink connection (e.g. 2026-10-07), so the autopilot's own log
replaces the telemetry.

* One log can hold several flights; it is split at the ARM/disarm events into one session per flight.
* ``TimeUS`` is the autopilot's boot clock; GPS week/milliseconds in ``GPS_0`` put it on UTC (GPS - 18 s).
* The main messages are logged at ~6.7 Hz (IMU ~17 Hz, GPS ~3.3 Hz); the table is on the ATT timestamps
  and the other messages are matched to the nearest sample.
* Units are ArduPilot's: degrees, m, m/s, V, A, mAh (BAT.CurrTot), mGauss (MAG), Pa (BARO.Press).
* BAT.Temp is 0 when the battery monitor has no temperature sensor; it is treated as missing.
"""

from __future__ import annotations

import io
import re
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd

from ..schema import FLIGHT_COLUMNS

NAME = "ardupilot_csv"
REQUIRED = {"ATT.csv", "POS.csv", "GPS_0.csv"}
GPS_EPOCH = pd.Timestamp("1980-01-06", tz="UTC")
GPS_UTC_LEAP_S = 18

COPTER_MODES = {0: "STABILIZE", 1: "ACRO", 2: "ALT_HOLD", 3: "AUTO", 4: "GUIDED", 5: "LOITER", 6: "RTL",
                7: "CIRCLE", 9: "LAND", 11: "DRIFT", 13: "SPORT", 14: "FLIP", 15: "AUTOTUNE", 16: "POSHOLD",
                17: "BRAKE", 18: "THROW", 19: "AVOID_ADSB", 20: "GUIDED_NOGPS", 21: "SMART_RTL", 22: "FLOWHOLD",
                23: "FOLLOW", 24: "ZIGZAG", 25: "SYSTEMID", 26: "AUTOROTATE", 27: "AUTO_RTL"}


class _Source:
    """Message CSVs from a zip or a folder."""

    def __init__(self, path: Path):
        self.path = path
        if path.is_dir():
            self.names = {p.name: p for p in path.rglob("*.csv")}
            self.zip = None
        else:
            self.zip = zipfile.ZipFile(path)
            self.names = {Path(n).name: n for n in self.zip.namelist() if n.endswith(".csv")}

    def has(self, name: str) -> bool:
        return name in self.names

    def read(self, name: str) -> pd.DataFrame | None:
        if name not in self.names:
            return None
        if self.zip is None:
            return pd.read_csv(self.names[name], low_memory=False)
        with self.zip.open(self.names[name]) as fh:
            return pd.read_csv(io.BytesIO(fh.read()), low_memory=False)

    def listing(self) -> list[str]:
        return sorted(self.names)


def is_export_dir(path: Path) -> bool:
    return path.is_dir() and REQUIRED <= {p.name for p in path.glob("*.csv")}


def sniff(path: Path) -> bool:
    try:
        if path.is_dir():
            return is_export_dir(path)
        if path.suffix.lower() == ".zip":
            with zipfile.ZipFile(path) as z:
                return REQUIRED <= {Path(n).name for n in z.namelist()}
    except (OSError, zipfile.BadZipFile):
        return False
    return False


def read(path: Path) -> tuple[pd.DataFrame, dict]:
    """The whole log as one session (see read_sessions for one session per flight)."""
    return read_sessions(path)[0]


def _asof(base: pd.DataFrame, msg: pd.DataFrame | None, cols: dict, tol_s: float = 0.6) -> None:
    """Add ``cols`` (src -> dst) of ``msg`` to ``base`` by nearest TimeUS."""
    if msg is None or not len(msg):
        for dst in cols.values():
            base[dst] = np.nan
        return
    m = msg[["TimeUS"] + [c for c in cols if c in msg.columns]].sort_values("TimeUS")
    m = m.rename(columns=cols)
    merged = pd.merge_asof(base[["TimeUS"]], m, on="TimeUS", direction="nearest",
                           tolerance=int(tol_s * 1e6))
    for dst in cols.values():
        base[dst] = merged[dst].to_numpy() if dst in merged.columns else np.nan


def read_sessions(path: Path) -> list[tuple[pd.DataFrame, dict]]:
    src = _Source(path)
    att = src.read("ATT.csv").sort_values("TimeUS").reset_index(drop=True)
    gps = src.read("GPS_0.csv")

    # Autopilot boot clock -> UTC via the GPS week / time of week.
    g = gps[(gps["GWk"] > 0) & (gps["GMS"] > 0)]
    gps_utc = GPS_EPOCH + pd.to_timedelta(g["GWk"] * 7, unit="D") + pd.to_timedelta(g["GMS"], unit="ms") \
        - pd.Timedelta(seconds=GPS_UTC_LEAP_S)
    offset_s = float(np.median((gps_utc - pd.Timestamp(0, tz="UTC")).dt.total_seconds() - g["TimeUS"] / 1e6))

    t = att[["TimeUS"]].copy()
    t["roll_deg"] = att["Roll"]
    t["pitch_deg"] = att["Pitch"]
    t["yaw_deg"] = ((att["Yaw"] + 180) % 360) - 180  # same -180..180 convention as the other readers
    t["heading_deg"] = att["Yaw"] % 360
    _asof(t, src.read("RATE.csv"), {"R": "roll_rate_deg_s", "P": "pitch_rate_deg_s", "Y": "yaw_rate_deg_s"})
    _asof(t, src.read("POS.csv"), {"Lat": "lat_deg", "Lng": "lon_deg", "Alt": "alt_msl_m", "RelHomeAlt": "alt_rel_m"})
    _asof(t, src.read("XKF1_0.csv"), {"VN": "vel_n_m_s", "VE": "vel_e_m_s", "VD": "vel_d_m_s"})
    _asof(t, gps, {"Lat": "gps_lat_deg", "Lng": "gps_lon_deg", "Alt": "gps_alt_m", "Spd": "gps_speed_m_s",
                   "GCrs": "gps_course_deg", "NSats": "gps_sats", "Status": "gps_fix_type", "HDop": "gps_hdop"})
    _asof(t, src.read("GPA_0.csv"), {"VDop": "gps_vdop"})
    _asof(t, src.read("CTUN.csv"), {"ThO": "throttle_pct"})
    t["throttle_pct"] = (t["throttle_pct"] * 100).round(2)
    _asof(t, src.read("IMU_0.csv"), {"AccX": "acc_x_m_s2", "AccY": "acc_y_m_s2", "AccZ": "acc_z_m_s2",
                                     "GyrX": "gyro_x_rad_s", "GyrY": "gyro_y_rad_s", "GyrZ": "gyro_z_rad_s"}, 0.2)
    _asof(t, src.read("MAG_0.csv"), {"MagX": "mag_x_mgauss", "MagY": "mag_y_mgauss", "MagZ": "mag_z_mgauss"})
    _asof(t, src.read("BAT_0.csv"), {"Volt": "batt_voltage_v", "Curr": "batt_current_a",
                                     "CurrTot": "batt_consumed_mah", "RemPct": "batt_remaining_pct",
                                     "Temp": "batt_temp_c"})
    t.loc[t["batt_temp_c"] == 0, "batt_temp_c"] = np.nan  # 0 = no temperature sensor
    _asof(t, src.read("RCOU.csv"), {"C1": "motor1_us", "C2": "motor2_us", "C3": "motor3_us", "C4": "motor4_us"})
    _asof(t, src.read("VIBE_0.csv"), {"VibeX": "vib_x_m_s2", "VibeY": "vib_y_m_s2", "VibeZ": "vib_z_m_s2",
                                      "Clip": "clip_0"})
    _asof(t, src.read("VIBE_1.csv"), {"Clip": "clip_1"})
    _asof(t, src.read("VIBE_2.csv"), {"Clip": "clip_2"})
    _asof(t, src.read("XKF4_0.csv"), {"SV": "ekf_vel_var", "SP": "ekf_pos_horiz_var", "SH": "ekf_pos_vert_var",
                                      "SM": "ekf_compass_var"})
    _asof(t, src.read("BARO_0.csv"), {"Press": "baro_press_hpa", "Temp": "baro_temp_c"})
    t["baro_press_hpa"] = t["baro_press_hpa"] / 100
    t["groundspeed_m_s"] = np.hypot(t["vel_n_m_s"], t["vel_e_m_s"]).round(3)
    t["climb_m_s"] = -t["vel_d_m_s"]

    # Flight mode and mission item: step functions of time.
    mode = src.read("MODE.csv")
    if mode is not None and len(mode):
        mm = mode.sort_values("TimeUS")[["TimeUS", "ModeNum"]]
        t["flight_mode"] = pd.merge_asof(t[["TimeUS"]], mm, on="TimeUS", direction="backward")["ModeNum"] \
            .map(lambda n: COPTER_MODES.get(int(n), f"MODE{int(n)}") if pd.notna(n) else None)
    msg = src.read("MSG.csv")
    if msg is not None and len(msg):
        reached = msg[msg["Message"].astype(str).str.startswith("Reached command #")].copy()
        reached["seq"] = reached["Message"].str.extract(r"#(\d+)", expand=False).astype(float)
        if len(reached):
            t["mission_seq"] = pd.merge_asof(t[["TimeUS"]], reached[["TimeUS", "seq"]].sort_values("TimeUS"),
                                             on="TimeUS", direction="backward")["seq"].to_numpy()

    # Armed state from ARM events (ArmState 1/0). Before the first event the state is the opposite of it.
    arm = src.read("ARM.csv")
    armed = pd.Series(False, index=t.index)
    events = arm.sort_values("TimeUS")[["TimeUS", "ArmState"]] if arm is not None and len(arm) else None
    if events is not None:
        first_state = 1 - int(events["ArmState"].iloc[0])
        state = pd.merge_asof(t[["TimeUS"]], events, on="TimeUS", direction="backward")["ArmState"]
        armed = state.fillna(first_state).astype(int).astype(bool)
    t["armed"] = armed.to_numpy()

    t["time_utc"] = pd.to_datetime((t["TimeUS"] / 1e6 + offset_s) * 1e9, utc=True).dt.round("ms")
    t["time_gps_utc"] = t["time_utc"]

    # Header-like information
    ver = src.read("VER.csv")
    fw = next((m for m in (msg["Message"].astype(str) if msg is not None else [])
               if re.match(r"Ardu\w+ V", m)), "")
    board = next((m for m in (msg["Message"].astype(str) if msg is not None else []) if "Cube" in m), "")
    header = {"format": "ArduPilot DataFlash log, CSV export (one file per message)",
              "format_version": fw or (f"{ver['Maj'].iloc[0]}.{ver['Min'].iloc[0]}.{ver['Pat'].iloc[0]}"
                                       if ver is not None and len(ver) else ""),
              "firmware": fw, "board": board.split(" ")[0] if board else "", "log": path.name}

    # Split at arm/disarm into flights: each armed period plus the disarmed time up to halfway to the next one.
    segments = _segments(t)
    missions = _missions(src.read("CMD.csv"))
    out = []
    for i, (a, b) in enumerate(segments, start=1):
        s = t.iloc[a:b].reset_index(drop=True).copy()
        s["elapsed_s"] = ((s["TimeUS"] - s["TimeUS"].iloc[0]) / 1e6).round(3)
        s["sample_n"] = np.arange(len(s))
        for c in FLIGHT_COLUMNS:
            if c not in s.columns:
                s[c] = np.nan
        s = s[list(FLIGHT_COLUMNS) + ["TimeUS"]].rename(columns={"TimeUS": "ap_time_us"})
        missing = [c for c in FLIGHT_COLUMNS if s[c].isna().all() and c not in ("north_m", "east_m", "time_local")]
        info = {
            "reader": NAME,
            "header": {**header, "session_id": f"{Path(path.name).stem}#{i}",
                       "segment": f"flight {i} of {len(segments)} in this log",
                       **_mission_for(missions, int(t["TimeUS"].iloc[b - 1]))},
            "gps_clock_offset_s": None,  # time is the autopilot's GPS-referenced clock
            "missing_columns": missing,
            "raw_columns": [f"{n[:-4]}" for n in src.listing()],
            "raw_hb_armed": None,
            "raw_rtc_utc": None,
            "notes": [
                "Recorded by the autopilot itself (onboard DataFlash log), not by the Pi logger: the Pi had no "
                "MAVLink connection on this day.",
                "The autopilot logs at about 6.7 Hz (GPS about 3.3 Hz), so plots are coarser than the 20 Hz Pi logs.",
                "No battery temperature: the autopilot's battery monitor has no temperature sensor.",
            ],
        }
        out.append((s, info))
    return out


def _missions(cmd: pd.DataFrame | None) -> list[dict]:
    """Missions uploaded to the autopilot (CMD rows; a new upload starts at CNum 0): the speeds set by
    DO_CHANGE_SPEED (MAV_CMD 178, param 2 in m/s) and the waypoint altitudes (frame 3 = above home)."""
    if cmd is None or not len(cmd):
        return []
    cmd = cmd.sort_values(["TimeUS", "CNum"]).reset_index(drop=True)
    out = []
    for _, m in cmd.groupby((cmd["CNum"] == 0).cumsum()):
        speeds = [float(v) for v in m.loc[m["CId"] == 178, "Prm2"] if v > 0]
        alts = sorted({float(a) for a in m.loc[(m["CId"] == 16) & (m["Frame"] == 3), "Alt"] if a > 0})
        out.append({"time_us": int(m["TimeUS"].iloc[0]), "speeds": speeds, "alts": alts})
    return out


def _mission_for(missions: list[dict], end_us: int) -> dict:
    """Header entries for the last mission uploaded before the end of a flight segment."""
    before = [m for m in missions if m["time_us"] <= end_us]
    if not before:
        return {}
    m = before[-1]
    g = lambda v: f"{v:g}"
    out = {"mission_source": "mission on the autopilot (CMD: DO_CHANGE_SPEED speeds, waypoint altitudes)"}
    if m["speeds"]:
        out["commanded_speed_ms"] = ", ".join(g(v) for v in m["speeds"])
    if m["alts"]:
        out["commanded_altitude_m"] = ", ".join(g(v) for v in m["alts"])
    return out


def _segments(t: pd.DataFrame) -> list[tuple[int, int]]:
    armed = t["armed"].to_numpy()
    if not armed.any():
        return [(0, len(t))]
    edges = np.flatnonzero(np.diff(armed.astype(int)) != 0) + 1
    starts = [0] + list(edges)
    ends = list(edges) + [len(t)]
    runs = [(a, b) for a, b in zip(starts, ends) if armed[a]]
    us = t["TimeUS"].to_numpy()
    # Between two flights, split where logging paused the longest (ArduPilot stops logging soon after
    # disarming); without a pause, split halfway.
    cuts = []
    for (a0, b0), (a1, b1) in zip(runs, runs[1:]):
        gaps = np.diff(us[b0 - 1:a1 + 1])
        k = int(np.argmax(gaps)) if len(gaps) else 0
        cuts.append(b0 + k if len(gaps) and gaps[k] > 5e6 else (b0 + a1) // 2)
    return list(zip([0] + cuts, cuts + [len(t)]))
