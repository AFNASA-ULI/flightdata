"""Flight summary numbers and data-quality checks, computed on the standard schema."""

from __future__ import annotations

import re

import numpy as np
import pandas as pd

from .clean import Session, airborne, armed_or_unknown
from .localtime import fmt
from .schema import FLIGHT_COLUMNS, GPS_FIX_TYPES

COMPASS_VAR_WARN = 0.5  # ArduPilot's default EKF failsafe threshold is 0.8
VIBE_WARN = 30.0  # m/s^2, ArduPilot's guidance for "too high"


def _r(x, n=1):
    return None if x is None or (isinstance(x, float) and np.isnan(x)) else round(float(x), n)


def mode_segments(df: pd.DataFrame) -> list[list]:
    m = pd.Series(df["flight_mode"].fillna("—").to_numpy(dtype=object), index=df.index)
    t = df["elapsed_s"]
    change = np.cumsum(m.to_numpy() != m.shift().to_numpy())
    segs = []
    for _, g in df.groupby(change, sort=True):
        segs.append([_r(t[g.index[0]], 2), _r(t[g.index[-1]], 2), m[g.index[0]]])
    for a, b in zip(segs, segs[1:]):
        a[1] = b[0]
    return segs


def summarize(s: Session) -> dict:
    df = s.flight
    t = df["elapsed_s"]
    air = airborne(df)
    armed = armed_or_unknown(df)
    out: dict = {
        "session_id": s.info["header"].get("session_id", s.stem),
        "platform": s.info["platform"]["label"],
        "platform_id": s.info["platform"]["id"],
        "start_utc": df["time_utc"].min().isoformat(),
        "end_utc": df["time_utc"].max().isoformat(),
        "start_local": df["time_local"].min().isoformat(),
        "end_local": df["time_local"].max().isoformat(),
        "duration_s": _r(t.max() - t.min()),
        "samples": int(len(df)),
        "sample_rate_hz": _r((len(df) - 1) / (t.max() - t.min()), 2) if len(df) > 1 else None,
        "modes": mode_segments(df),
    }
    if air.any():
        out["takeoff_s"] = _r(t[air].min())
        out["landing_s"] = _r(t[air].max())
        out["airborne_s"] = _r(t[air].max() - t[air].min())
        out["landed_in_log"] = bool(t[air].max() < t.max() - 1.0)
    out["max_alt_rel_m"] = _r(df["alt_rel_m"].max())
    rng = np.hypot(df["north_m"], df["east_m"])
    out["max_range_m"] = _r(rng.max(), 0)

    auto = df["flight_mode"].eq("AUTO")
    cruise = air & auto & (df["groundspeed_m_s"] > 2)
    if cruise.any():
        out["cruise_alt_m"] = _r(df.loc[cruise, "alt_rel_m"].median())
        out["cruise_speed_m_s"] = _r(df.loc[cruise, "groundspeed_m_s"].median())
        out["cruise_current_a"] = _r(df.loc[cruise, "batt_current_a"].median())
    seq = df.loc[auto & air, "mission_seq"].dropna()
    out["mission_items"] = int(seq[seq > 0].nunique()) if len(seq) else 0
    rtl = df.index[df["flight_mode"].eq("RTL") & (t > out.get("takeoff_s", -1))]
    out["rtl_s"] = _r(t[rtl[0]]) if len(rtl) else None

    # "At rest": disarmed, or (when the log has no armed flag) drawing almost no current.
    rest = ~armed if df["armed"].notna().any() else (df["batt_current_a"] < 1.0)
    mah = df["batt_consumed_mah"].dropna()
    out["batt_consumed_mah"] = _r(mah.max() - mah.min(), 0) if len(mah) else None
    rest_before = df.loc[rest & (t < out.get("takeoff_s", t.max())), "batt_voltage_v"]
    rest_after = df.loc[rest & (t > out.get("landing_s", t.max())), "batt_voltage_v"]
    out["batt_v_start"] = _r(rest_before.median(), 2) if len(rest_before) else _r(df["batt_voltage_v"].iloc[0], 2)
    out["batt_v_end"] = _r(rest_after.median(), 2) if len(rest_after) else _r(df["batt_voltage_v"].iloc[-1], 2)
    out["batt_v_end_at_rest"] = bool(len(rest_after))
    out["gps"] = gps_summary(df, air)
    return out


def gps_summary(df: pd.DataFrame, air: pd.Series) -> dict:
    """Share of time in each GPS fix type (while airborne, else over the whole log), satellites, HDOP."""
    rows = air if air.any() else pd.Series(True, index=df.index)
    fix = df.loc[rows, "gps_fix_type"].dropna().astype(int)
    out: dict = {"basis": "airborne" if air.any() else "whole log",
                 "sats_min": _r(df.loc[rows, "gps_sats"].min(), 0),
                 "hdop_max": _r(df.loc[rows, "gps_hdop"].max(), 2)}
    if len(fix):
        pct = (fix.value_counts(normalize=True) * 100).sort_index(ascending=False)
        out["fix"] = [{"type": int(k), "name": GPS_FIX_TYPES.get(int(k), (f"type {k}", None))[0],
                       "accuracy": GPS_FIX_TYPES.get(int(k), (None, None))[1], "pct": round(float(v), 1)}
                      for k, v in pct.items()]
    else:
        out["fix"] = []
    out.update(height_check(df, air))
    return out


def height_check(df: pd.DataFrame, air: pd.Series) -> dict:
    """Takeoff elevation from RTK (on the ground before takeoff, RTK Fixed) and how far the barometric
    relative altitude strays from the RTK height in flight."""
    fixed = df["gps_fix_type"] == 6
    if not air.any():
        return {}
    t0 = df.loc[air, "elapsed_s"].min()
    pre = fixed & ~air & (df["elapsed_s"] < t0) & (df["groundspeed_m_s"].fillna(0) < 0.3)
    out: dict = {}
    if pre.sum() >= 20:
        home = float((df["gps_alt_m"] - df["alt_rel_m"])[pre].median())
        out["home_msl_m"] = round(home, 2)
        out["home_msl_source"] = "RTK GPS before takeoff"
        err = (df["alt_rel_m"] - (df["gps_alt_m"] - home))[air & fixed]
        if len(err) >= 20:
            out["baro_minus_rtk_m"] = {"median": _r(err.median(), 2), "p5": _r(err.quantile(0.05), 2),
                                       "p95": _r(err.quantile(0.95), 2)}
    else:
        both = df[["alt_msl_m", "alt_rel_m"]].dropna()
        both = both[both["alt_msl_m"] > 1]
        if len(both):
            out["home_msl_m"] = round(float((both["alt_msl_m"] - both["alt_rel_m"]).median()), 2)
            out["home_msl_source"] = "EKF (approximate)"
    return out


def run_checks(s: Session, summary: dict) -> list[dict]:
    """Return a list of {"level": "warn"|"info", "title", "detail"} findings."""
    df, info, h = s.flight, s.info, s.info["header"]
    air = airborne(df)
    found: list[dict] = []

    def add(level, title, detail):
        found.append({"level": level, "title": title, "detail": detail})

    # --- wind sensor overlap
    if s.wind is None:
        add("info", "No wind-drone data", "There are no wind-drone logs in raw_data/wind_drone/.")
    else:
        w = s.wind_info
        if w["rows"] == 0:
            day = fmt(w["flight_start_gps_utc"], "%Y-%m-%d")
            same_day = [lg for lg in w.get("logs_available", []) if fmt(lg["start_utc"], "%Y-%m-%d") == day]
            if same_day:
                t = lambda x: fmt(x, "%H:%M:%S")
                spans = ", ".join(f"{t(lg['start_utc'])}–{t(lg['end_utc'])}" for lg in same_day)
                detail = (f"That day's wind log(s) cover {spans} {fmt(w['flight_start_gps_utc'], '%Z')}; this flight "
                          f"runs {t(w['flight_start_gps_utc'])}–{t(w['flight_end_gps_utc'])} (GPS time).")
                if any(lg.get("bag_closed") is False for lg in same_day):
                    detail += (" The wind bag was not closed cleanly (no MCAP end marker), so the recording probably "
                               "continued past the end of this file; look for a complete copy on the wind drone.")
                add("warn", "No wind data for this flight", detail)
            else:
                add("info", "No wind data for this flight", "No wind-drone log was recorded on this day.")
        elif w["coverage_pct"] < 99:
            add("warn", "Wind log only partly overlaps this flight",
                f"It covers {w['coverage_pct']}% of the flight.")
    if s.hwas is None:
        add("info", "No HWAS weather-station data", "No HWAS export in raw_data/hwas_data/ covers this flight.")

    # --- reader notes (known quirks of this log format)
    for note in info.get("notes", []):
        add("info", "Log format note", note)

    # --- clocks
    rtc = info.get("raw_rtc_utc")
    if rtc is not None and rtc.notna().any():
        first = str(rtc.dropna().iloc[0])
        parsed = pd.to_datetime(first, errors="coerce", utc=True)
        start = df["time_utc"].min()
        if pd.notna(parsed) and abs((parsed - start).total_seconds()) > 86400:
            add("warn", "Real-time clock (RTC) not set",
                f"RTC_UTC reads {first}, but the flight is on {start.date()}.")
    spreadsheet = []
    if h.get("_trailing_commas"):
        spreadsheet.append("the # header lines end in runs of commas")
    if rtc is not None and rtc.notna().any() and not re.match(r"^\d{4}-\d{2}-\d{2}", str(rtc.dropna().iloc[0])):
        spreadsheet.append(f"RTC_UTC reads “{rtc.dropna().iloc[0]}” instead of an ISO date")
    if spreadsheet:
        add("warn", "Raw file looks re-saved by a spreadsheet program",
            "; ".join(spreadsheet)[0].upper() + "; ".join(spreadsheet)[1:] + ". Excel does both when it re-saves a "
            "CSV, and it can also round values. Compare with the original file from the logger.")

    # --- session id vs start time
    sid = h.get("session_id", "")
    m = re.search(r"(\d{8})T(\d{6})Z", sid)
    if m and h.get("start_utc", "")[:10].replace("-", "") != m.group(1):
        add("warn", "Session ID date doesn't match the start time",
            f"session_id is {sid}, but start_utc is {h.get('start_utc')}. The logger probably reused an old session ID.")

    # --- heartbeat mixing
    hb = info.get("raw_hb_armed")
    if hb is not None:
        raw_toggles = int(hb.dropna().diff().abs().sum())
        clean_toggles = int(df["armed"].fillna(False).astype(int).diff().abs().sum())
        if raw_toggles > 4 * max(clean_toggles, 1):
            add("info", "HB_Armed mixes heartbeats from several MAVLink components",
                f"The raw HB_Armed column flips {raw_toggles} times. The cleaned armed column only uses autopilot "
                f"heartbeats (base_mode bit 0 set) and changes {clean_toggles} time{'s' if clean_toggles != 1 else ''}. The logger should filter "
                "heartbeats to the autopilot's sysid/compid.")

    # --- unit labels
    if any(c.endswith("mag_mT") for c in info["raw_columns"]):
        add("info", "Magnetometer columns mislabeled",
            "IMU_*mag_mT holds MAVLink SCALED_IMU values, which are milligauss (Earth's field is ≈500 mG, ≈0.05 mT). "
            "The cleaned data calls them mag_*_mgauss; the logger's column names should be fixed.")

    # --- landing in log
    if summary.get("takeoff_s") is not None and not summary.get("landed_in_log", True):
        add("info", "Log ends while airborne", "The log stops before the vehicle landed.")

    # --- empty / constant columns (raw columns kept in the cleaned data)
    raw_cols = [c for c in info["raw_columns"] if c in df.columns]
    empty = [c for c in raw_cols if df[c].isna().all()]
    mapped_empty = [c for c in FLIGHT_COLUMNS if df[c].isna().all() and c not in info["missing_columns"]]
    if empty or mapped_empty:
        cols = empty + mapped_empty
        add("info", "Empty columns", f"{len(cols)} column(s) have no data: " + ", ".join(cols[:30])
            + (" …" if len(cols) > 30 else ""))
    # --- battery temperature sensor enabled but silent
    if h.get("temp_enabled", "").lower() == "true" and df["batt_temp_c"].isna().all():
        add("warn", "No battery temperature recorded",
            "The header says temp_enabled: True"
            + (f" (i2c_unavailable: {h['i2c_unavailable']})" if h.get("i2c_unavailable") else "")
            + ", but Battery_Temp_C is empty for the whole log. Check the sensor and its wiring.")

    # --- RTK fix held?
    fixes = {f["type"]: f for f in summary.get("gps", {}).get("fix", [])}
    if 6 in fixes and fixes[6]["pct"] < 99:
        others = "; ".join(f"{f['name']} {f['pct']:.0f}%" + (f" ({f['accuracy']})" if f["accuracy"] else "")
                           for t, f in fixes.items() if t != 6)
        add("warn" if fixes[6]["pct"] < 90 else "info", "RTK fix not held for the whole flight",
            f"RTK Fixed (cm-level vs. base) for {fixes[6]['pct']:.0f}% of the {summary['gps']['basis']} time; "
            f"the rest was {others}. Positions in those stretches are less accurate; the GPS panel shows when.")

    # --- barometric vs RTK height
    b = summary.get("gps", {}).get("baro_minus_rtk_m")
    if b and max(abs(b["p5"]), abs(b["p95"])) >= 0.5:
        add("info", "Barometric altitude differs from RTK height",
            f"alt_rel_m (EKF, barometer-based) minus the RTK GPS height above takeoff: median {b['median']:+.1f} m, "
            f"5–95% range {b['p5']:+.1f} to {b['p95']:+.1f} m in flight. For precise height use gps_alt_m while "
            "the fix is RTK Fixed.")

    # --- EKF / vibration / GPS
    cv = df["ekf_compass_var"].max()
    if pd.notna(cv) and cv >= COMPASS_VAR_WARN:
        t = df.loc[df["ekf_compass_var"].idxmax(), "elapsed_s"]
        add("warn", "High compass variance",
            f"EKF compass variance peaks at {cv:.2f} (t = {t:.1f} s). ArduPilot's default failsafe threshold is 0.8.")
    vmax = df[["vib_x_m_s2", "vib_y_m_s2", "vib_z_m_s2"]].max().max()
    if pd.notna(vmax) and vmax >= VIBE_WARN:
        add("warn", "High vibration", f"Peak vibration is {vmax:.1f} m/s² (guidance: keep below {VIBE_WARN:.0f}).")
    clips = df[["clip_0", "clip_1", "clip_2"]].max() - df[["clip_0", "clip_1", "clip_2"]].min()
    if clips.fillna(0).sum() > 0:
        add("warn", "Accelerometer clipping", "Clip counters increased during the log: "
            + ", ".join(f"{k} +{int(v)}" for k, v in clips.items() if v > 0))
    if air.any():
        sats = df.loc[air, "gps_sats"].min()
        hdop = df.loc[air, "gps_hdop"].max()
        if (pd.notna(sats) and sats < 8) or (pd.notna(hdop) and hdop > 1.5):
            add("warn", "Weak GPS in flight", f"Minimum {sats:.0f} satellites, maximum HDOP {hdop:.2f} while airborne.")

    # --- logger timing
    if "Slots_Skipped" in df.columns:
        skipped = pd.to_numeric(df["Slots_Skipped"], errors="coerce").sum()
        if skipped > 0:
            add("info", "Logger skipped samples", f"{int(skipped)} scheduled sample slot(s) were skipped.")

    order = {"warn": 0, "info": 1}
    return sorted(found, key=lambda f: order[f["level"]])
