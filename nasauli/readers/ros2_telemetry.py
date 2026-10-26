"""Reader for ROS 2 telemetry recordings from the onboard computer (``cube_telemetry_node`` relaying the
autopilot over MAVLink, ``pi_peripherals_node`` with the Pi's own sensors). First used for the Aurelia X6.

Accepted layouts (one recording per folder):

* the team's ROS 2 CSV export: one CSV per topic, named after the topic (``/cube_telemetry_node/imu/raw`` ->
  ``cube_telemetry_node__imu__raw.csv``), first column ``bag_time_ns``, then the message fields;
* the ROS 2 bag itself (a folder with one ``.mcap``), decoded without ROS by ``nasauli.bagtools`` into
  exactly the same columns as the CSV export.

Topics and units (as recorded):

==========================================  ======  ==============================================================
topic                                       rate    used for
==========================================  ======  ==============================================================
cube_telemetry_node/imu/raw                 100 Hz  acc (m/s², body frame, z ≈ -g at rest), gyro (rad/s)
cube_telemetry_node/imu/filtered            100 Hz  attitude quaternion -> roll/pitch/yaw, body rates
cube_telemetry_node/imu/mag                 100 Hz  magnetic field in tesla (-> mgauss)
cube_telemetry_node/motors/pwm              50 Hz   servo outputs 1-8 in µs (channels that stay 0 are dropped)
cube_telemetry_node/battery                 10 Hz   voltage, current, percentage (0-1); temperature 0 = no sensor
cube_telemetry_node/gps/raw                 10 Hz   GPS position (status -1 = no fix)
cube_telemetry_node/gps/filtered            10 Hz   EKF position (0, 0 = none)
cube_telemetry_node/gps/status              10 Hz   fix type, satellites, HDOP/VDOP, accuracies, relative altitude
cube_telemetry_node/ekf_status              2 Hz    EKF flags, variances, status message
pi_peripherals_node/tmp119/battN_tempM      1 Hz    TMP119 battery temperature sensors (2 per battery)
==========================================  ======  ==============================================================

The topics are put on one 20 Hz time grid (the Pi logger's rate on the other drones): faster topics are
averaged over each 50 ms step, slower ones are interpolated (numbers) or held (states such as the fix type).
"""

from __future__ import annotations

import csv
import io
import re
from pathlib import Path

import numpy as np
import pandas as pd

from ..schema import FLIGHT_COLUMNS

NAME = "ros2_telemetry"
GRID_HZ = 20.0
KEY_TOPIC = "cube_telemetry_node__imu__raw"

DEG = 180.0 / np.pi
TESLA_TO_MGAUSS = 1e7


# ------------------------------------------------------------------ finding a recording
def _csvs(path: Path) -> dict[str, Path]:
    return {p.stem: p for p in sorted(path.glob("*.csv"))}


def _mcap(path: Path) -> Path | None:
    files = sorted(path.glob("*.mcap"))
    return files[0] if len(files) == 1 else None


def _mcap_topics(path: Path) -> set[str]:
    from mcap.records import Channel, Message
    from mcap.stream_reader import StreamReader

    topics = set()
    try:
        with open(path, "rb") as f:
            for rec in StreamReader(f, skip_magic=False).records:
                if isinstance(rec, Channel):
                    topics.add(rec.topic.strip("/").replace("/", "__"))
                elif isinstance(rec, Message):
                    break
    except Exception:
        pass
    return topics


def is_export_dir(path: Path) -> bool:
    """A folder holding one recording of these topics (CSV export or bag)."""
    if not path.is_dir():
        return False
    if KEY_TOPIC in _csvs(path):
        return True
    m = _mcap(path)
    return m is not None and KEY_TOPIC in _mcap_topics(m)


def sniff(path: Path) -> bool:
    return is_export_dir(path)


def _load_topics(path: Path) -> dict[str, pd.DataFrame]:
    """topic name (``node__topic``) -> table with the CSV export's columns."""
    csvs = _csvs(path)
    if KEY_TOPIC in csvs:
        return {k: pd.read_csv(p, low_memory=False) for k, p in csvs.items()}
    from .. import bagtools

    topics, *_ = bagtools.read_bag(_mcap(path))
    out = {}
    for t in topics.values():
        if not t.rows:
            continue
        d = t.defs[bagtools._full(t.type)]
        buf = io.StringIO()  # same text as the CSV export, parsed the same way
        w = csv.writer(buf, lineterminator="\n")
        for i, (lt, m) in enumerate(sorted(t.rows, key=lambda r: r[0])):
            cols = bagtools.flatten(m, d, t.defs)
            if i == 0:
                w.writerow(["bag_time_ns"] + [k for k, _ in cols])
            w.writerow([lt] + [v for _, v in cols])
        buf.seek(0)
        out[t.name.strip("/").replace("/", "__")] = pd.read_csv(buf, low_memory=False)
    return out


# ------------------------------------------------------------------ parsing helpers
def _kv(series: pd.Series, key: str) -> pd.Series:
    """Value of one diagnostic_msgs/KeyValue inside a DiagnosticStatus repr, as text."""
    return series.astype(str).str.extract(rf"key='{re.escape(key)}', value='([^']*)'")[0]


def _num(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s, errors="coerce")


def _array(series: pd.Series) -> pd.DataFrame:
    """``array('i', [1100, 1100, ...])`` -> one column per element."""
    inner = series.astype(str).str.extract(r"\[(.*)\]")[0].fillna("")
    return inner.str.split(",", expand=True).apply(lambda c: pd.to_numeric(c.str.strip(), errors="coerce"))


def _euler(qx, qy, qz, qw):
    """Quaternion -> roll, pitch, yaw (ZYX, degrees)."""
    roll = np.arctan2(2 * (qw * qx + qy * qz), 1 - 2 * (qx * qx + qy * qy))
    pitch = np.arcsin(np.clip(2 * (qw * qy - qz * qx), -1, 1))
    yaw = np.arctan2(2 * (qw * qz + qx * qy), 1 - 2 * (qy * qy + qz * qz))
    return roll * DEG, pitch * DEG, (yaw * DEG) % 360


# ------------------------------------------------------------------ resampling onto one grid
def _resample(t: np.ndarray, frame: pd.DataFrame, grid: np.ndarray, hold: set[str] = frozenset(),
              circular: set[str] = frozenset()) -> pd.DataFrame:
    """Put one topic (times ``t`` in seconds, sorted) on ``grid``.

    Faster than the grid: mean of the samples within ±half a step of each grid time (circular mean for angles).
    Slower: linear interpolation, or the last value for ``hold`` columns; no value across gaps longer than
    three of the topic's own sample periods.
    """
    out = pd.DataFrame(index=range(len(grid)))
    if len(t) == 0:
        return out.assign(**{c: np.nan for c in frame.columns})
    period = float(np.median(np.diff(t))) if len(t) > 1 else 1.0
    step = 1.0 / GRID_HZ
    if period < step * 0.75:
        k = np.floor((t - grid[0]) / step + 0.5).astype(int)
        ok = (k >= 0) & (k < len(grid))
        g = frame[ok].groupby(k[ok])
        for c in frame.columns:
            if c in circular:
                r = np.radians(frame[c][ok])
                m = np.degrees(np.arctan2(np.sin(r).groupby(k[ok]).mean(), np.cos(r).groupby(k[ok]).mean())) % 360
            else:
                m = g[c].mean()
            out[c] = m.reindex(range(len(grid))).to_numpy()
        return out
    idx = np.searchsorted(t, grid, side="right") - 1  # last sample at or before each grid time
    nxt = np.clip(idx + 1, 0, len(t) - 1)
    prev = np.clip(idx, 0, len(t) - 1)
    max_gap = 3 * period
    inside = (idx >= 0) & (grid <= t[-1] + period)
    for c in frame.columns:
        v = frame[c].to_numpy(dtype=float)
        if c in hold or c in circular:
            val = np.where(inside & (grid - t[prev] <= max_gap), v[prev], np.nan)
        else:
            gap = t[nxt] - t[prev]
            w = np.where(gap > 0, (grid - t[prev]) / np.where(gap > 0, gap, 1), 0.0)
            val = v[prev] + np.clip(w, 0, 1) * (v[nxt] - v[prev])
            val = np.where(inside & (idx < len(t) - 1) & (gap <= max_gap), val,
                           np.where(inside & (grid - t[prev] <= period), v[prev], np.nan))
        out[c] = val
    return out


# ------------------------------------------------------------------ reading
def read(path: Path) -> tuple[pd.DataFrame, dict]:
    topics = _load_topics(path)
    times = {k: _num(df["bag_time_ns"]).to_numpy(dtype=float) / 1e9 for k, df in topics.items()}
    t0 = min(t.min() for t in times.values() if len(t))
    t1 = max(t.max() for t in times.values() if len(t))
    grid = t0 + np.arange(int(np.floor((t1 - t0) * GRID_HZ)) + 1) / GRID_HZ
    parts: list[pd.DataFrame] = []
    used: dict[str, str] = {}

    def add(topic: str, frame: pd.DataFrame, hold=frozenset(), circular=frozenset()):
        df = topics[topic]
        order = np.argsort(times[topic], kind="stable")
        r = _resample(times[topic][order], frame.iloc[order].reset_index(drop=True), grid, set(hold), set(circular))
        parts.append(r)
        used[topic] = f"{len(df)} messages, {len(df) / max(float(np.ptp(times[topic])), 1e-9):.1f} Hz"

    if "cube_telemetry_node__imu__raw" in topics:
        d = topics["cube_telemetry_node__imu__raw"]
        add("cube_telemetry_node__imu__raw", pd.DataFrame({
            "acc_x_m_s2": _num(d["linear_acceleration.x"]), "acc_y_m_s2": _num(d["linear_acceleration.y"]),
            "acc_z_m_s2": _num(d["linear_acceleration.z"]), "gyro_x_rad_s": _num(d["angular_velocity.x"]),
            "gyro_y_rad_s": _num(d["angular_velocity.y"]), "gyro_z_rad_s": _num(d["angular_velocity.z"])}))
    if "cube_telemetry_node__imu__filtered" in topics:
        d = topics["cube_telemetry_node__imu__filtered"]
        roll, pitch, yaw = _euler(*(_num(d[f"orientation.{a}"]).to_numpy() for a in "xyzw"))
        add("cube_telemetry_node__imu__filtered", pd.DataFrame({
            "roll_deg": roll, "pitch_deg": pitch, "yaw_deg": yaw,
            "roll_rate_deg_s": _num(d["angular_velocity.x"]) * DEG, "pitch_rate_deg_s": _num(d["angular_velocity.y"]) * DEG,
            "yaw_rate_deg_s": _num(d["angular_velocity.z"]) * DEG}), circular={"yaw_deg"})
    if "cube_telemetry_node__imu__mag" in topics:
        d = topics["cube_telemetry_node__imu__mag"]
        add("cube_telemetry_node__imu__mag", pd.DataFrame(
            {f"mag_{a}_mgauss": _num(d[f"magnetic_field.{a}"]) * TESLA_TO_MGAUSS for a in "xyz"}))
    if "cube_telemetry_node__motors__pwm" in topics:
        d = topics["cube_telemetry_node__motors__pwm"]
        pwm = _array(d["data"])
        pwm = pwm.loc[:, (pwm.fillna(0) != 0).any()]  # unused channels stay 0
        pwm.columns = [f"motor{i + 1}_us" for i in pwm.columns]
        add("cube_telemetry_node__motors__pwm", pwm)
    if "cube_telemetry_node__battery" in topics:
        d = topics["cube_telemetry_node__battery"]
        pct = _num(d["percentage"])
        add("cube_telemetry_node__battery", pd.DataFrame({
            "batt_voltage_v": _num(d["voltage"]), "batt_current_a": _num(d["current"]),
            "batt_remaining_pct": pct * 100 if pct.max() <= 1.0 else pct,
            "batt_ap_temp_c": _num(d["temperature"]).where(lambda s: s != 0)}))  # 0 = no sensor
    if "cube_telemetry_node__gps__raw" in topics:
        d = topics["cube_telemetry_node__gps__raw"]
        fix = (_num(d["status.status"]) >= 0) & (_num(d["latitude"]) != 0)
        add("cube_telemetry_node__gps__raw", pd.DataFrame({
            "gps_lat_deg": _num(d["latitude"]).where(fix), "gps_lon_deg": _num(d["longitude"]).where(fix),
            "gps_alt_m": _num(d["altitude"]).where(fix)}))
    if "cube_telemetry_node__gps__filtered" in topics:
        d = topics["cube_telemetry_node__gps__filtered"]
        pos = _num(d["latitude"]) != 0
        add("cube_telemetry_node__gps__filtered", pd.DataFrame({
            "lat_deg": _num(d["latitude"]).where(pos), "lon_deg": _num(d["longitude"]).where(pos),
            "alt_msl_m": _num(d["altitude"]).where(pos)}))
    if "cube_telemetry_node__gps__status" in topics:
        s = topics["cube_telemetry_node__gps__status"]["status"]
        add("cube_telemetry_node__gps__status", pd.DataFrame({
            "gps_fix_type": _num(_kv(s, "fix_type")), "gps_sats": _num(_kv(s, "satellites_visible")),
            "gps_hdop": _num(_kv(s, "hdop")), "gps_vdop": _num(_kv(s, "vdop")),
            "gps_h_acc_m": _num(_kv(s, "h_acc_m")), "gps_v_acc_m": _num(_kv(s, "v_acc_m")),
            "alt_rel_m": _num(_kv(s, "alt_rel_m"))}), hold={"gps_fix_type", "gps_sats"})
    ekf_msgs = None
    if "cube_telemetry_node__ekf_status" in topics:
        s = topics["cube_telemetry_node__ekf_status"]["status"]
        add("cube_telemetry_node__ekf_status", pd.DataFrame({
            "ekf_flags": _num(_kv(s, "flags")), "ekf_vel_var": _num(_kv(s, "velocity_variance")),
            "ekf_pos_horiz_var": _num(_kv(s, "pos_horiz_variance")), "ekf_pos_vert_var": _num(_kv(s, "pos_vert_variance")),
            "ekf_compass_var": _num(_kv(s, "compass_variance"))}), hold={"ekf_flags"})
        ekf_msgs = s.astype(str).str.extract(r"message='([^']*)'")[0].value_counts()
    temps = sorted(k for k in topics if k.startswith("pi_peripherals_node__tmp119__"))
    for k in temps:
        name = k.removeprefix("pi_peripherals_node__tmp119__")  # batt1_temp1
        add(k, pd.DataFrame({f"{name}_c": _num(topics[k]["temperature"])}))

    data = pd.concat(parts, axis=1)
    tcols = [f"{k.removeprefix('pi_peripherals_node__tmp119__')}_c" for k in temps]
    if tcols:
        data["batt_temp_c"] = data[tcols].mean(axis=1).round(4)
    for c in data.columns:
        if data[c].dtype.kind == "f":
            data[c] = data[c].round(6)

    out = pd.DataFrame({"time_utc": pd.to_datetime(np.round(grid * 1e9).astype("int64"), utc=True)})
    out["time_gps_utc"] = out["time_utc"]  # no GPS time to check the computer's clock against (see notes)
    out["elapsed_s"] = np.round(grid - grid[0], 3)
    out["sample_n"] = np.arange(len(out))
    out["armed"] = pd.NA
    out = pd.concat([out, data], axis=1)
    for c in FLIGHT_COLUMNS:
        if c not in out.columns:
            out[c] = np.nan
    out = out[list(FLIGHT_COLUMNS) + [c for c in out.columns if c not in FLIGHT_COLUMNS]]

    start = pd.Timestamp(int(t0 * 1e9), tz="UTC")
    header = {
        "format": "ROS 2 telemetry recording (cube_telemetry_node, pi_peripherals_node)",
        "session_id": path.name,
        "platform": "",  # from the folder name (platforms.py)
        "recording": "CSV export of a ROS 2 bag" if KEY_TOPIC in _csvs(path) else "ROS 2 bag (.mcap)",
        "start_utc": start.isoformat(),
        "topics": ", ".join(sorted(topics)),
    }
    missing = [c for c in FLIGHT_COLUMNS if out[c].isna().all() and c not in ("north_m", "east_m", "time_local", "armed")]
    notes = [
        "Recorded on the onboard computer as ROS 2 topics; put on one 20 Hz time grid for the processed data "
        "(topics run at " + ", ".join(sorted({v.split(', ')[1] for v in used.values()}, key=lambda x: -float(x[:-3])))
        + "; faster topics are averaged over each 50 ms step, slower ones interpolated). The raw topic files keep "
        "every message.",
        "Time is the onboard computer's clock when each message was recorded (ROS bag time). It is not checked "
        "against GPS time.",
        "No armed state or flight mode is recorded in these topics.",
        "Battery temperature (batt_temp_c) is the mean of the TMP119 sensors on the batteries; each sensor is kept "
        "in its own column (" + ", ".join(tcols) + ")." if tcols else "No battery temperature sensors recorded.",
    ]
    if ekf_msgs is not None and len(ekf_msgs):
        notes.append("EKF status message: " + "; ".join(f"“{m}” ({n}×)" for m, n in ekf_msgs.head(3).items()) + ".")
    info = {
        "reader": NAME,
        "header": header,
        "gps_clock_offset_s": None,
        "missing_columns": missing,
        "raw_columns": [],
        "raw_hb_armed": None,
        "raw_rtc_utc": None,
        "topics": used,
        "notes": notes,
    }
    return out, info
