"""Reader for the wind-drone ROS 2 bags.

Layout under ``raw_data/wind_drone/``::

    <bag_name>/<bag_name>_0.mcap (+ metadata.yaml)       the bag itself
    <bag_name>_csv/wind_sensor_node__wind.csv           optional CSV export of the same bag
    <bag_name>_csv/wind_sensor_node__temperature.csv

A recording is read from its CSV export when there is one, otherwise straight from the .mcap (messages are
decoded with the schema stored in the bag, so no ROS install is needed; a bag that was never closed is read up
to where it stops). Rows that appear in more than one source are dropped.

``bag_time_ns`` is when the wind-drone computer recorded the message; ``header.stamp`` is set by
the sensor node and is quantised to the sensor's 5 Hz output. Both are kept. ``vector.x`` is wind
speed and ``vector.y`` wind direction; ``vector.z`` has been 0 in every file so far.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

NAME = "wind_ros2_csv"
WIND_FILE = "wind_sensor_node__wind.csv"
TEMP_FILE = "wind_sensor_node__temperature.csv"


MCAP_MAGIC = b"\x89MCAP0\r\n"


def bag_closed(csv_dir: Path) -> bool | None:
    """Whether the bag this CSV was exported from was closed cleanly (MCAP files end with the magic bytes).

    A missing end marker means the recorder never finished the file (power loss, killed process, or a copy
    taken while it was still recording), so the recording may be longer than this file. None if no bag.
    """
    bag_dir = csv_dir.with_name(csv_dir.name.removesuffix("_csv"))  # also works when given the bag folder
    files = sorted(bag_dir.glob("*.mcap"))
    if not files:
        return None
    for f in files:
        with open(f, "rb") as fh:
            fh.seek(max(0, f.stat().st_size - len(MCAP_MAGIC)))
            if fh.read() != MCAP_MAGIC:
                return False
    return True


def find(raw_dir: Path) -> list[Path]:
    """CSV-export folders under raw_dir."""
    return sorted(p.parent for p in raw_dir.glob(f"*/{WIND_FILE}"))


def recordings(raw_dir: Path) -> list[Path]:
    """One entry per recording: its CSV-export folder if it has one, else its bag folder."""
    csv_dirs = find(raw_dir)
    exported = {d.name.removesuffix("_csv") for d in csv_dirs}
    bags = sorted(p.parent for p in raw_dir.glob("*/*.mcap") if p.parent.name not in exported)
    return sorted(csv_dirs + [b for b in bags if b not in csv_dirs], key=lambda p: p.name)


def read_any(path: Path) -> pd.DataFrame:
    return read(path) if (path / WIND_FILE).exists() else read_mcap(path)


def read_mcap(bag_dir: Path) -> pd.DataFrame:
    """Wind and temperature messages straight from a bag's .mcap file(s)."""
    import io

    from mcap.records import Channel, Message, Schema
    from mcap.stream_reader import StreamReader
    from mcap_ros2.decoder import DecoderFactory

    wind, temp = [], []
    for f in sorted(bag_dir.glob("*.mcap")):
        schemas, channels, decoders = {}, {}, {}
        factory = DecoderFactory()
        reader = StreamReader(io.BytesIO(f.read_bytes()), skip_magic=False)
        try:
            for rec in reader.records:
                if isinstance(rec, Schema):
                    schemas[rec.id] = rec
                elif isinstance(rec, Channel):
                    channels[rec.id] = rec
                elif isinstance(rec, Message):
                    ch = channels.get(rec.channel_id)
                    if ch is None or ch.topic not in ("/wind_sensor_node/wind", "/wind_sensor_node/temperature"):
                        continue
                    if ch.id not in decoders:
                        decoders[ch.id] = factory.decoder_for(ch.message_encoding, schemas[ch.schema_id])
                    m = decoders[ch.id](rec.data)
                    if ch.topic.endswith("/wind"):
                        wind.append((rec.log_time, m.header.stamp.sec * 1_000_000_000 + m.header.stamp.nanosec,
                                     m.vector.x, m.vector.y, m.vector.z))
                    else:
                        temp.append((rec.log_time, m.temperature))
        except Exception:  # a bag that was never closed ends mid-record; keep what was read
            pass
    df = pd.DataFrame(wind, columns=["t", "stamp", "x", "y", "z"])
    out = pd.DataFrame({
        "time_utc": pd.to_datetime(df["t"], unit="ns", utc=True),
        "sensor_stamp_utc": pd.to_datetime(df["stamp"], unit="ns", utc=True),
        "wind_speed_m_s": df["x"].astype(float), "wind_dir_deg": df["y"].astype(float),
        "wind_z": df["z"].astype(float)}).sort_values("time_utc")
    t = pd.DataFrame(temp, columns=["t", "temperature_c"])
    t = pd.DataFrame({"time_utc": pd.to_datetime(t["t"], unit="ns", utc=True),
                      "temperature_c": t["temperature_c"].astype(float)}).sort_values("time_utc")
    if len(out) and len(t):
        out = pd.merge_asof(out, t, on="time_utc", direction="nearest", tolerance=pd.Timedelta("1s"))
    else:
        out["temperature_c"] = float("nan")
    return out.reset_index(drop=True)


def read(csv_dir: Path) -> pd.DataFrame:
    w = pd.read_csv(csv_dir / WIND_FILE)
    df = pd.DataFrame(
        {
            "time_utc": pd.to_datetime(w["bag_time_ns"], unit="ns", utc=True),
            "sensor_stamp_utc": pd.to_datetime(
                w["header.stamp.sec"] * 1_000_000_000 + w["header.stamp.nanosec"], unit="ns", utc=True
            ),
            "wind_speed_m_s": w["vector.x"].astype(float),
            "wind_dir_deg": w["vector.y"].astype(float),
            "wind_z": w["vector.z"].astype(float),
        }
    )
    tpath = csv_dir / TEMP_FILE
    if tpath.exists():
        t = pd.read_csv(tpath)
        temp = pd.DataFrame(
            {
                "time_utc": pd.to_datetime(t["bag_time_ns"], unit="ns", utc=True),
                "temperature_c": t["temperature"].astype(float),
            }
        ).sort_values("time_utc")
        df = pd.merge_asof(
            df.sort_values("time_utc"), temp, on="time_utc", direction="nearest",
            tolerance=pd.Timedelta("1s"),
        )
    else:
        df["temperature_c"] = float("nan")
    return df.sort_values("time_utc").reset_index(drop=True)
