"""Standard (canonical) flight-data schema.

Every reader converts its raw log format into these columns, with these names and units. Code
downstream of the readers (cleaning, checks, reports, notebooks) only relies on this schema, so a
change to the logger's format only requires a new reader.

A column that a given log does not contain is still present, filled with NaN, so the schema is the
same for every flight. Raw columns that are not mapped here are carried through unchanged under
their original (CamelCase) names, so no information is dropped.
"""

# name: (unit, description)
FLIGHT_COLUMNS = {
    # time
    "time_utc": ("UTC", "Logger host clock time of the sample"),
    "time_gps_utc": ("UTC", "Sample time corrected to the autopilot's GPS clock"),
    "time_local": ("America/Denver", "time_gps_utc in Mountain Time (MST/MDT)"),
    "elapsed_s": ("s", "Time since logging started"),
    "sample_n": ("-", "Sample counter"),
    # state
    "armed": ("bool", "Autopilot armed, from autopilot heartbeats only"),
    "flight_mode": ("-", "Autopilot flight mode name"),
    "mission_seq": ("-", "Current mission item"),
    "wp_dist_m": ("m", "Distance to current waypoint"),
    # attitude
    "roll_deg": ("deg", "Roll angle"),
    "pitch_deg": ("deg", "Pitch angle"),
    "yaw_deg": ("deg", "Yaw angle"),
    "roll_rate_deg_s": ("deg/s", "Roll rate"),
    "pitch_rate_deg_s": ("deg/s", "Pitch rate"),
    "yaw_rate_deg_s": ("deg/s", "Yaw rate"),
    # fused position (EKF)
    "lat_deg": ("deg", "Latitude (EKF)"),
    "lon_deg": ("deg", "Longitude (EKF)"),
    "alt_msl_m": ("m", "Altitude above mean sea level (EKF; its reference is set at startup and can be off by meters)"),
    "alt_rel_m": ("m", "Altitude relative to home (EKF, barometer-based)"),
    "vel_n_m_s": ("m/s", "Velocity north"),
    "vel_e_m_s": ("m/s", "Velocity east"),
    "vel_d_m_s": ("m/s", "Velocity down"),
    "heading_deg": ("deg", "Heading"),
    "north_m": ("m", "Position north of home"),
    "east_m": ("m", "Position east of home"),
    # raw GPS
    "gps_lat_deg": ("deg", "Latitude (raw GPS)"),
    "gps_lon_deg": ("deg", "Longitude (raw GPS)"),
    "gps_alt_m": ("m", "Altitude MSL (raw GPS; RTK height when the fix is RTK)"),
    "gps_speed_m_s": ("m/s", "Ground speed (raw GPS)"),
    "gps_course_deg": ("deg", "Course over ground (raw GPS)"),
    "gps_sats": ("-", "Satellites used"),
    "gps_fix_type": ("-", "MAVLink GPS fix type: 3 = 3D, 4 = DGPS, 5 = RTK Float, 6 = RTK Fixed"),
    "gps_hdop": ("-", "Horizontal dilution of precision"),
    "gps_vdop": ("-", "Vertical dilution of precision"),
    # VFR HUD
    "groundspeed_m_s": ("m/s", "Ground speed"),
    "airspeed_m_s": ("m/s", "Airspeed (estimated on a multirotor)"),
    "climb_m_s": ("m/s", "Climb rate"),
    "throttle_pct": ("%", "Throttle"),
    # IMU
    "acc_x_m_s2": ("m/s^2", "Accelerometer X"),
    "acc_y_m_s2": ("m/s^2", "Accelerometer Y"),
    "acc_z_m_s2": ("m/s^2", "Accelerometer Z"),
    "gyro_x_rad_s": ("rad/s", "Gyro X"),
    "gyro_y_rad_s": ("rad/s", "Gyro Y"),
    "gyro_z_rad_s": ("rad/s", "Gyro Z"),
    "mag_x_mgauss": ("mgauss", "Magnetometer X"),
    "mag_y_mgauss": ("mgauss", "Magnetometer Y"),
    "mag_z_mgauss": ("mgauss", "Magnetometer Z"),
    # power
    "batt_voltage_v": ("V", "Battery voltage"),
    "batt_current_a": ("A", "Battery current"),
    "batt_consumed_mah": ("mAh", "Charge consumed"),
    "batt_remaining_pct": ("%", "Battery remaining (autopilot estimate)"),
    "batt_temp_c": ("degC", "Battery temperature (logger's own sensor on the battery)"),
    # motors
    "motor1_us": ("us", "Servo/motor output 1"),
    "motor2_us": ("us", "Servo/motor output 2"),
    "motor3_us": ("us", "Servo/motor output 3"),
    "motor4_us": ("us", "Servo/motor output 4"),
    # vibration
    "vib_x_m_s2": ("m/s^2", "Vibration X"),
    "vib_y_m_s2": ("m/s^2", "Vibration Y"),
    "vib_z_m_s2": ("m/s^2", "Vibration Z"),
    "clip_0": ("count", "Accelerometer 0 clipping events"),
    "clip_1": ("count", "Accelerometer 1 clipping events"),
    "clip_2": ("count", "Accelerometer 2 clipping events"),
    # EKF
    "ekf_flags": ("-", "EKF status flags"),
    "ekf_vel_var": ("-", "EKF velocity variance"),
    "ekf_pos_horiz_var": ("-", "EKF horizontal position variance"),
    "ekf_pos_vert_var": ("-", "EKF vertical position variance"),
    "ekf_compass_var": ("-", "EKF compass variance"),
    # baro
    "baro_press_hpa": ("hPa", "Barometric pressure"),
    "baro_temp_c": ("degC", "Barometer temperature"),
    # autopilot wind estimate
    # The autopilot does not measure wind: WND_* is an estimate that stays at 0 / -180 in these logs. Kept for
    # completeness only; wind comes from the wind drone and the HWAS station (wind.*, hwas.*).
    "ap_wind_dir_deg": ("deg", "Not used: autopilot wind estimate (not a measurement), direction"),
    "ap_wind_speed_m_s": ("m/s", "Not used: autopilot wind estimate (not a measurement), speed"),
    "ap_wind_speed_z_m_s": ("m/s", "Not used: autopilot wind estimate (not a measurement), vertical speed"),
}

HWAS_COLUMNS = {
    "time_utc": ("UTC", "Station time (reported in Mountain Time, converted)"),
    "time_local": ("America/Denver", "Station time in Mountain Time"),
    "elapsed_s": ("s", "Seconds on the matching flight's elapsed_s axis"),
    "wind_speed_m_s": ("m/s", "Wind speed (station reports whole knots)"),
    "wind_dir_deg": ("deg", "Direction the wind comes from"),
    "gust_m_s": ("m/s", "Gust speed (0 = no gust reported)"),
    "temperature_c": ("degC", "Air temperature (station reports whole °F)"),
    "humidity_pct": ("%", "Relative humidity"),
    "pressure_sealevel_pa": ("Pa", "Pressure as reported (~101.5 kPa, so sea-level-adjusted, not station pressure)"),
    "source": ("-", "HWAS export file the row came from"),
}

# MAVLink GPS_FIX_TYPE -> (name, typical horizontal accuracy). Rules of thumb for open sky, not guarantees.
# RTK accuracies are relative to the base station: with a base that is not on a surveyed point, the whole
# solution can be offset from true coordinates by up to a few meters (the same offset for the whole session).
GPS_FIX_TYPES = {
    0: ("No GPS", None),
    1: ("No fix", None),
    2: ("2D fix", "> 5 m"),
    3: ("3D fix", "~2–5 m"),
    4: ("DGPS/SBAS", "~0.5–2 m"),
    5: ("RTK Float", "~0.2–1 m vs. base"),
    6: ("RTK Fixed", "cm-level vs. base"),
    7: ("Static", None),
    8: ("PPP", "~0.1 m"),
}

# How the RTK base station was set up for the flights so far (shown on the website).
RTK_BASE = ("Emlid Reach RS2+ base station. Its position was not a surveyed point, so RTK positions are "
            "precise relative to the base (centimeters) but the absolute coordinates can be offset by up to a "
            "few meters. The offset is the same throughout a session.")

WIND_COLUMNS = {
    "time_utc": ("UTC", "Time the wind-drone computer received the message (ROS bag time)"),
    "sensor_stamp_utc": ("UTC", "Timestamp in the message header, set by the sensor node"),
    "time_local": ("America/Denver", "time_utc in Mountain Time (MST/MDT)"),
    "elapsed_s": ("s", "Seconds on the matching flight's elapsed_s axis"),
    "wind_speed_m_s": ("m/s", "Wind speed (vector.x)"),
    "wind_dir_deg": ("deg", "Wind direction (vector.y)"),
    "wind_z": ("-", "vector.z (unused by the sensor, always 0 so far)"),
    "temperature_c": ("degC", "Wind sensor temperature"),
}
