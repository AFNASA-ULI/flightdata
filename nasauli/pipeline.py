"""raw_data/<flight>/ -> processed/<flight>/ (flight + wind tables as Parquet and CSV, metadata.json)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pandas as pd

from . import __version__
from .checks import run_checks, summarize
from .clean import RAW, Session, find_flight_dirs, load_session, raw_logs
from .schema import FLIGHT_COLUMNS, HWAS_COLUMNS, WIND_COLUMNS

PROCESSED = "processed"


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _write_table(df: pd.DataFrame, base: Path) -> None:
    df.to_parquet(base.with_suffix(".parquet"), index=False)
    csv = df.copy()
    for c in csv.columns:
        if isinstance(csv[c].dtype, pd.DatetimeTZDtype):
            if str(csv[c].dt.tz) == "UTC":
                csv[c] = csv[c].dt.strftime("%Y-%m-%dT%H:%M:%S.%f").str[:-3] + "Z"
            else:  # local time: keep the UTC offset, e.g. 2026-09-25T10:39:58.882-06:00
                off = csv[c].dt.strftime("%z")
                csv[c] = (csv[c].dt.strftime("%Y-%m-%dT%H:%M:%S.%f").str[:-3]
                          + off.str[:3] + ":" + off.str[3:])
    csv.to_csv(base.with_suffix(".csv"), index=False)


def output_dir(root: Path, flight_id: str) -> Path:
    return root / PROCESSED / flight_id


def output_names(prefix: str) -> dict[str, str]:
    """File names inside processed/<flight>/. ``prefix`` is "" when the folder holds one session."""
    p = f"{prefix}_" if prefix else ""
    return {"flight": f"{p}flight", "wind": f"{p}wind", "hwas": f"{p}hwas", "metadata": f"{p}metadata.json"}


def process_session(s: Session, root: Path, prefix: str = "") -> dict:
    out = output_dir(root, s.flight_id)
    out.mkdir(parents=True, exist_ok=True)
    names = output_names(prefix)
    summary = summarize(s)
    checks = run_checks(s, summary)

    _write_table(s.flight, out / names["flight"])
    files = [f"{names['flight']}.parquet", f"{names['flight']}.csv"]
    for key, table in (("wind", s.wind), ("hwas", s.hwas)):
        path = out / names[key]
        if table is not None and len(table):
            _write_table(table, path)
            files += [f"{names[key]}.parquet", f"{names[key]}.csv"]
        else:
            for ext in (".parquet", ".csv"):
                path.with_suffix(ext).unlink(missing_ok=True)
    files.append(names["metadata"])

    header = {k: v for k, v in s.info["header"].items() if not k.startswith("_")}
    meta = {
        "pipeline_version": __version__,
        "flight_id": s.flight_id,
        "platform": s.info["platform"],
        "reader": s.info["reader"],
        "source": {"file": f"{RAW}/{s.flight_id}/{s.source.name}", "sha256": _sha256(s.source),
                   "bytes": s.source.stat().st_size},
        "files": files,
        "header": header,
        "summary": summary,
        "checks": checks,
        "home": s.info["home"],
        "gps_clock_offset_s": s.info["gps_clock_offset_s"],
        "missing_columns": s.info["missing_columns"],
        "wind": s.wind_info or None,
        "hwas": s.hwas_info or None,
        "columns": {k: {"unit": u, "description": d} for k, (u, d) in FLIGHT_COLUMNS.items()},
        "wind_columns": {k: {"unit": u, "description": d} for k, (u, d) in WIND_COLUMNS.items()}
        if s.wind is not None and len(s.wind) else None,
        "hwas_columns": {k: {"unit": u, "description": d} for k, (u, d) in HWAS_COLUMNS.items()}
        if s.hwas is not None and len(s.hwas) else None,
        "notes": "Columns not listed under 'columns' are raw logger columns carried through unchanged.",
    }
    (out / names["metadata"]).write_text(json.dumps(meta, indent=2, default=str) + "\n")
    return meta


def process_flight_dir(flight_dir: Path, root: Path, log=print) -> list[dict]:
    sessions = []
    for csv_path in raw_logs(flight_dir):
        s = load_session(csv_path, root)
        if s is None:
            log(f"  skip {csv_path.name}: no reader for this format")
            continue
        sessions.append(s)
    metas = []
    for s in sessions:
        meta = process_session(s, root, prefix="" if len(sessions) == 1 else s.stem)
        n_warn = sum(c["level"] == "warn" for c in meta["checks"])
        log(f"  {s.source.name} [{meta['reader']}]: {len(s.flight)} rows, {n_warn} warning(s), "
            f"wind rows {s.wind_info.get('rows', 0)}")
        metas.append(meta)
    return metas


def process_all(root: Path, log=print) -> None:
    for d in find_flight_dirs(root):
        log(d.name)
        process_flight_dir(d, root, log)


def load_all_metadata(root: Path) -> list[dict]:
    """Metadata of every processed session, each with ``_dir`` (processed/<flight>) and ``_prefix``."""
    metas = []
    for m in sorted((root / PROCESSED).glob("*/*metadata.json")):
        meta = json.loads(m.read_text())
        meta["_dir"] = m.parent
        meta["_prefix"] = m.name[: -len("metadata.json")].rstrip("_")
        metas.append(meta)
    return metas
