"""Repair a wind-drone ROS 2 bag folder: rebuild ``metadata.yaml`` and write the ``<bag>_csv`` export.

    python -m nasauli repair-bag raw_data/wind_drone/<bag_name> [--force]

Works without a ROS install: messages are decoded with the type definitions stored in the .mcap
(mcap-ros2-support), and a bag that was never closed is read up to where it stops.

* ``metadata.yaml`` is written in the rosbag2 (version 9, jazzy) format the recorder produces, with the
  topics' QoS profiles and type hashes taken from the bag's channel records. The per-file ``message_count``
  is the true count (the recorder writes twice the count there; a known rosbag2 quirk).
* The CSV export matches the team's existing exports: one file per topic named after the topic
  (``/wind_sensor_node/wind`` -> ``wind_sensor_node__wind.csv``), first column ``bag_time_ns`` (log time), then
  the message fields in definition order, nested messages flattened with dots, and values written the way the
  ROS 2 Python message classes print them (``array('f', [...])``, ``diagnostic_msgs.msg.KeyValue(...)``, ...).
"""

from __future__ import annotations

import array
import csv
import io
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

# ------------------------------------------------------------------ message definitions (ros2msg)
PRIMITIVES = {
    "bool", "byte", "char", "float32", "float64", "int8", "uint8", "int16", "uint16", "int32", "uint32",
    "int64", "uint64", "string", "wstring",
}
ARRAY_CODES = {"float32": "f", "float64": "d", "int8": "b", "uint8": "B", "int16": "h", "uint16": "H",
               "int32": "i", "uint32": "I", "int64": "q", "uint64": "Q"}


@dataclass
class Field:
    name: str
    type: str  # e.g. float32, string, std_msgs/Header
    seq: bool = False  # unbounded/bounded sequence (T[] or T[<=N])
    fixed: int | None = None  # fixed-size array length (T[N])

    @property
    def primitive(self) -> bool:
        return self.type in PRIMITIVES or self.type.startswith("string<=")


@dataclass
class MsgDef:
    name: str  # pkg/msg/Type
    fields: list[Field] = field(default_factory=list)


def parse_msgdefs(top_name: str, text: str) -> dict[str, MsgDef]:
    """Parse a ros2msg schema (top-level definition followed by '====' / 'MSG: pkg/Type' sections)."""
    defs: dict[str, MsgDef] = {}
    blocks = text.split("\n================================================================================\n")
    for i, block in enumerate(blocks):
        lines = block.strip("\n").splitlines()
        if i == 0:
            name, body = top_name, lines
        else:
            name, body = lines[0].removeprefix("MSG: ").strip(), lines[1:]
        pkg = name.split("/")[0]
        d = MsgDef(_full(name))
        for line in body:
            code = line.split("#", 1)[0]
            parts = code.split()
            if len(parts) < 2 or "=" in code:  # blank/comment, or a constant (TYPE NAME = value)
                continue
            ftype, fname = parts[0], parts[1]
            seq, fixed = False, None
            if ftype.endswith("]"):
                base, dim = ftype[:-1].split("[", 1)
                if dim == "" or dim.startswith("<="):
                    seq = True
                else:
                    fixed = int(dim)
                ftype = base
            if ftype not in PRIMITIVES and not ftype.startswith("string<="):
                if ftype == "Header":
                    ftype = "std_msgs/Header"
                elif "/" not in ftype:
                    ftype = f"{pkg}/{ftype}"
                ftype = _full(ftype)
            d.fields.append(Field(fname, ftype, seq, fixed))
        defs[d.name] = d
    return defs


def _full(name: str) -> str:
    """'std_msgs/Header' -> 'std_msgs/msg/Header'."""
    parts = name.split("/")
    return f"{parts[0]}/msg/{parts[-1]}" if len(parts) == 2 else name


# ------------------------------------------------------------------ value formatting (rclpy style)
def _py_type_name(full: str) -> str:
    return full.replace("/", ".")  # sensor_msgs/msg/NavSatFix -> sensor_msgs.msg.NavSatFix


def _scalar(f: Field, v):
    if f.type == "byte":
        return bytes([v]) if isinstance(v, int) else bytes(v)
    if f.type == "char":
        return int(v) if not isinstance(v, str) else v
    if f.type == "bool":
        return bool(v)
    return v


def _arr(f: Field, v):
    code = ARRAY_CODES.get(f.type)
    if f.fixed is not None and code:
        return np.array(list(v), dtype={"f": np.float32, "d": np.float64}.get(code, None))
    if code:
        return array.array(code, list(v))
    return [_scalar(f, x) for x in v]


def msg_repr(v, d: MsgDef, defs: dict[str, MsgDef]) -> str:
    """repr() of a ROS 2 Python message object."""
    args = []
    for f in d.fields:
        x = getattr(v, f.name)
        if f.primitive:
            if f.seq or f.fixed is not None:
                val = _arr(f, x)
                if isinstance(val, array.array):
                    s = "[]" if len(val) == 0 else repr(val)[len(f"array('{val.typecode}', "):-1]
                else:
                    s = repr(val)
            else:
                s = repr(_scalar(f, x))
        else:
            sub = defs[f.type]
            if f.seq or f.fixed is not None:
                s = "[" + ", ".join(msg_repr(e, sub, defs) for e in x) + "]"
            else:
                s = msg_repr(x, sub, defs)
        args.append(f"{f.name}={s}")
    return f"{_py_type_name(d.name)}({', '.join(args)})"


def flatten(v, d: MsgDef, defs: dict[str, MsgDef], prefix: str = "") -> list[tuple[str, str]]:
    """Columns for one message: nested messages flattened with dots, everything else str()-ed."""
    out = []
    for f in d.fields:
        key = prefix + f.name
        x = getattr(v, f.name)
        if f.primitive:
            if f.seq or f.fixed is not None:
                out.append((key, str(_arr(f, x))))
            else:
                s = _scalar(f, x)
                out.append((key, str(s)))
        else:
            sub = defs[f.type]
            if f.seq or f.fixed is not None:
                items = [msg_repr(e, sub, defs) for e in x]
                out.append((key, items[0] if len(items) == 1 else "[" + ", ".join(items) + "]"))
            else:
                out.extend(flatten(x, sub, defs, key + "."))
    return out


# ------------------------------------------------------------------ bag reading
@dataclass
class Topic:
    channel_id: int
    name: str
    type: str
    qos: str
    type_hash: str
    defs: dict
    rows: list = field(default_factory=list)
    count: int = 0


def read_bag(mcap_path: Path):
    from mcap.records import Channel, Message, Schema
    from mcap.stream_reader import StreamReader
    from mcap_ros2.decoder import DecoderFactory

    schemas, topics, decoders = {}, {}, {}
    factory = DecoderFactory()
    first = last = None
    closed = mcap_path.read_bytes()[-8:] == b"\x89MCAP0\r\n"
    reader = StreamReader(io.BytesIO(mcap_path.read_bytes()), skip_magic=False)
    try:
        for rec in reader.records:
            if isinstance(rec, Schema):
                schemas[rec.id] = rec
            elif isinstance(rec, Channel) and rec.id not in topics:
                sch = schemas[rec.schema_id]
                topics[rec.id] = Topic(rec.id, rec.topic, sch.name, rec.metadata.get("offered_qos_profiles", ""),
                                       rec.metadata.get("topic_type_hash", ""),
                                       parse_msgdefs(sch.name, sch.data.decode()))
            elif isinstance(rec, Message):
                t = topics[rec.channel_id]
                if rec.channel_id not in decoders:
                    ch_schema = schemas[next(s for s in schemas if schemas[s].name == t.type)]
                    decoders[rec.channel_id] = factory.decoder_for("cdr", ch_schema)
                m = decoders[rec.channel_id](rec.data)
                t.rows.append((rec.log_time, m))
                t.count += 1
                first = rec.log_time if first is None else min(first, rec.log_time)
                last = rec.log_time if last is None else max(last, rec.log_time)
    except Exception:  # unclosed bag: stop at the cut
        pass
    return topics, first, last, closed


# ------------------------------------------------------------------ writers
def write_csv_export(topics: dict, out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for t in topics.values():
        if not t.rows:
            continue
        d = t.defs[_full(t.type)]
        path = out_dir / (t.name.strip("/").replace("/", "__") + ".csv")
        with open(path, "w", newline="") as fh:
            w = csv.writer(fh, lineterminator="\n")
            header = None
            for log_time, m in sorted(t.rows, key=lambda r: r[0]):
                cols = flatten(m, d, t.defs)
                if header is None:
                    header = ["bag_time_ns"] + [k for k, _ in cols]
                    w.writerow(header)
                w.writerow([log_time] + [v for _, v in cols])
        written.append(path)
    return written


def metadata_yaml(bag_name: str, file_name: str, topics: dict, first: int, last: int) -> str:
    total = sum(t.count for t in topics.values())
    dur = (last - first) if first is not None else 0
    start = first if first is not None else 0
    lines = ["rosbag2_bagfile_information:", "  version: 9", "  storage_identifier: mcap",
             "  duration:", f"    nanoseconds: {dur}", "  starting_time:",
             f"    nanoseconds_since_epoch: {start}", f"  message_count: {total}", "  topics_with_message_count:"]
    for t in sorted(topics.values(), key=lambda t: -t.channel_id):  # the recorder lists newest channel first
        lines += ["    - topic_metadata:", f"        name: {t.name}", f"        type: {t.type}",
                  "        serialization_format: cdr", "        offered_qos_profiles:"]
        lines += ["          " + ln for ln in t.qos.rstrip("\n").splitlines()]
        lines += [f"        type_description_hash: {t.type_hash}", f"      message_count: {t.count}"]
    lines += ['  compression_format: ""', '  compression_mode: ""', "  relative_file_paths:", f"    - {file_name}",
              "  files:", f"    - path: {file_name}", "      starting_time:",
              f"        nanoseconds_since_epoch: {start}", "      duration:", f"        nanoseconds: {dur}",
              f"      message_count: {total}", "  custom_data: ~", "  ros_distro: jazzy"]
    return "\n".join(lines)


def repair(bag_dir: Path, force: bool = False, log=print) -> None:
    files = sorted(bag_dir.glob("*.mcap"))
    if len(files) != 1:
        raise SystemExit(f"{bag_dir}: expected one .mcap file, found {len(files)}")
    topics, first, last, closed = read_bag(files[0])
    log(f"{bag_dir.name}: {sum(t.count for t in topics.values())} messages on {len(topics)} topics"
        + ("" if closed else " (bag was not closed; read up to where it stops)"))
    meta = bag_dir / "metadata.yaml"
    if force or not meta.exists() or meta.stat().st_size == 0:
        meta.write_text(metadata_yaml(bag_dir.name, files[0].name, topics, first, last))
        log(f"  wrote {meta}")
    else:
        log(f"  {meta} exists; use --force to overwrite")
    out = bag_dir.with_name(bag_dir.name + "_csv")
    if force or not out.exists():
        for p in write_csv_export(topics, out):
            log(f"  wrote {p}")
    else:
        log(f"  {out} exists; use --force to overwrite")
