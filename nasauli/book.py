"""Build the website: a Jupyter Book with generated pages for every drone, flight and raw log.

    book/                   hand-written pages and config (intro.md, contributing.md, _config.yml, _static/)
    notebooks/              copied in as tutorials
    processed/, raw_data/   read to generate experiments/, data/ and _toc.yml

Output: a static HTML site (``--out``, default ``_site``) with the per-flight interactive reports under
reports/ and zip downloads under downloads/.
"""

from __future__ import annotations

import html
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pandas as pd
import yaml

from . import __version__
from .clean import RAW, WIND_DIR, find_flight_dirs, load_hwas_logs, load_wind_logs
from .pipeline import PROCESSED, load_all_metadata
from .localtime import LOCAL_LABEL, fmt
from .platforms import PLATFORMS
from .report import render, stat_tiles, title_for
from .readers import hwas as hwas_reader
from .schema import FLIGHT_COLUMNS, HWAS_COLUMNS, RTK_BASE, WIND_COLUMNS

REPO = "https://github.com/AFNASA-ULI/flightdata"
BRANCH = "main"


def blob(path: str) -> str:
    return f"{REPO}/blob/{BRANCH}/{path}"


def raw_url(path: str) -> str:
    return f"{REPO}/raw/{BRANCH}/{path}"


def tree(path: str) -> str:
    return f"{REPO}/tree/{BRANCH}/{path}"


def size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return ""


def esc(s) -> str:
    """Escape text for a Markdown table cell."""
    return str(s).replace("|", "\\|").replace("\n", " ")


def when(meta: dict) -> str:
    return fmt(meta["summary"]["start_utc"])


def page_id(meta: dict) -> str:
    return meta["flight_id"] + (f"__{meta['_prefix']}" if meta["_prefix"] else "")


def wind_cell(meta: dict) -> str:
    w = meta.get("wind")
    if not w:
        return "–"
    return f"{w['coverage_pct']:.0f}%" if w["rows"] else "none"


def n_warn(meta: dict) -> int:
    return sum(c["level"] == "warn" for c in meta["checks"])


def fnum(x, fmt="{:.0f}", unit=""):
    return "–" if x is None else fmt.format(x) + unit


class Book:
    def __init__(self, root: Path, src: Path):
        self.root = root
        self.src = src
        self.metas = sorted(load_all_metadata(root), key=lambda m: m["summary"]["start_utc"], reverse=True)
        self.by_platform: dict[str, list[dict]] = {}
        for m in self.metas:
            self.by_platform.setdefault(m["platform"]["id"], []).append(m)
        self.platform_order = sorted(self.by_platform, key=lambda p: self.by_platform[p][0]["platform"]["label"])

    def write(self, rel: str, text: str) -> None:
        p = self.src / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text.strip() + "\n")

    def flight_table(self, metas: list[dict], link_prefix: str, drone_col: bool = False) -> str:
        head = f"| Date ({LOCAL_LABEL}) | " + ("Drone | " if drone_col else "") + "Airborne | Max range | Max alt | Wind data | Warnings |"
        sep = "|---|" + ("---|" if drone_col else "") + "---:|---:|---:|---|---:|"
        rows = [head, sep]
        for m in metas:
            sm = m["summary"]
            rows.append(
                f"| [{when(m)}]({link_prefix}{page_id(m)}.md) | "
                + (f"{esc(m['platform']['label'])} | " if drone_col else "")
                + f"{fnum(sm.get('airborne_s'), unit=' s')} | {fnum(sm.get('max_range_m'), unit=' m')} | "
                f"{fnum(sm.get('max_alt_rel_m'), '{:.1f}', ' m')} | {wind_cell(m)} | {n_warn(m)} |")
        return "\n".join(rows)

    # ---------------------------------------------------------------- experiments
    def experiments(self) -> list[dict]:
        toc = [{"file": "experiments/index"}]
        parts = ["# All flights", "",
                 "Every flight, grouped by drone and sorted newest first. Each flight page has summary numbers, "
                 "data-quality findings, interactive plots and downloads.", ""]
        for pid in self.platform_order:
            ms = self.by_platform[pid]
            p = ms[0]["platform"]
            parts += [f"## {p['label']}", "", f"{p.get('description') or ''} {len(ms)} flight(s). "
                      f"[Drone page]({pid}/index.md)", "", self.flight_table(ms, f"{pid}/"), ""]
            self.drone_page(pid, ms)
            toc.append({"file": f"experiments/{pid}/index",
                        "sections": [{"file": f"experiments/{pid}/{page_id(m)}"} for m in ms]})
            for m in ms:
                self.flight_page(pid, m)
        if not self.metas:
            parts.append("No processed flights yet.")
        self.write("experiments/index.md", "\n".join(parts))
        return toc

    def drone_page(self, pid: str, ms: list[dict]) -> None:
        p = ms[0]["platform"]
        airborne = sum(m["summary"].get("airborne_s") or 0 for m in ms)
        text = [f"# {p['label']}", "",
                f"{p.get('kind') or ''}{'. ' if p.get('kind') else ''}{p.get('description') or ''}", "",
                f"{len(ms)} flight(s), {airborne / 60:.0f} min airborne in total. "
                f'Download all of this drone\'s processed data: <a href="../../downloads/{pid}_processed.zip">{pid}_processed.zip</a>',
                "", self.flight_table(ms, "")]
        self.write(f"experiments/{pid}/index.md", "\n".join(text))

    def flight_page(self, pid: str, m: dict) -> None:
        d = m["_dir"]
        pre = f"{m['_prefix']}_" if m["_prefix"] else ""
        wind = pd.read_parquet(d / f"{pre}wind.parquet") if (d / f"{pre}wind.parquet").exists() else None
        hw = pd.read_parquet(d / f"{pre}hwas.parquet") if (d / f"{pre}hwas.parquet").exists() else None
        tiles = "\n".join(
            f'<div class="stat"><div class="k">{html.escape(k)}</div><div class="v">{html.escape(v)}</div>'
            f'<div class="s">{html.escape(s)}</div></div>' for k, v, s in stat_tiles(m, wind, hw))
        sm = m["summary"]
        rel = f"{PROCESSED}/{m['flight_id']}/"
        text = [
            f"# {when(m)}", "",
            f'<p class="flight-meta">{html.escape(m["platform"]["label"])} · flight <code>{m["flight_id"]}</code> · '
            f'session <code>{html.escape(sm["session_id"])}</code> · {sm["duration_s"]:.0f} s logged at '
            f'{sm["sample_rate_hz"]:.0f} Hz</p>', "",
            f'<div class="flight-stats">\n{tiles}\n</div>', "",
        ]
        warns = [c for c in m["checks"] if c["level"] == "warn"]
        infos = [c for c in m["checks"] if c["level"] != "warn"]
        if warns or infos:
            text += ["## Things to check", ""]
            for c in warns:
                text += [f":::{{admonition}} {c['title']}", ":class: warning", "", c["detail"], ":::", ""]
            if infos:
                text += [f":::{{admonition}} {len(infos)} more note(s) about this log", ":class: note dropdown", ""]
                text += [f"- **{c['title']}.** {c['detail']}" for c in infos]
                text += [":::", ""]
        report = f"../../reports/{page_id(m)}.html"
        text += ["## Plots", "",
                 "Zoom any time plot and the others follow; the zoomed time range is highlighted on the ground track. "
                 f'<a href="{report}" target="_blank">Open the plots in their own page ↗</a>', "",
                 f'<iframe class="flight-report" src="{report}" title="Interactive plots" loading="lazy"></iframe>', "",
                 "## Data", "",
                 "| File | Contents | Size |", "|---|---|---:|"]
        labels = {"flight.parquet": "Cleaned flight data (Parquet)", "flight.csv": "Cleaned flight data (CSV)",
                  "wind.parquet": "Wind data for this flight (Parquet)", "wind.csv": "Wind data for this flight (CSV)",
                  "hwas.parquet": "HWAS weather station during this flight (Parquet)",
                  "hwas.csv": "HWAS weather station during this flight (CSV)",
                  "metadata.json": "Summary, findings, column units, provenance"}
        for f in m["files"]:
            label = labels.get(f.removeprefix(pre), f)
            text.append(f"| [{f}]({raw_url(rel + f)}) | {label} | {size((d / f).stat().st_size)} |")
        src = m["source"]
        text += [f"| [{Path(src['file']).name}]({raw_url(src['file'])}) | Raw log, as recorded | {size(src['bytes'])} |", "",
                 f"More about the raw log: [raw data page](../../data/raw/{page_id(m)}.md). "
                 f"Column definitions: [processed data](../../data/processed.md)."]
        self.write(f"experiments/{pid}/{page_id(m)}.md", "\n".join(text))

    # ---------------------------------------------------------------- raw data
    def raw_pages(self) -> dict:
        sections = []
        text = ["# Raw data", "",
                "The logs exactly as they came off the drones. They are never edited; cleaning happens in the "
                f"processing step. Browse them on [GitHub]({tree(RAW)}) or open a flight below for its file list, "
                "header information and a column overview.", ""]
        by_folder = {m["flight_id"]: m for m in self.metas}
        for pid in self.platform_order:
            ms = self.by_platform[pid]
            text += [f"## {ms[0]['platform']['label']}", "", f"| Flight | Date ({LOCAL_LABEL}) | Files | Size | Format |",
                     "|---|---|---:|---:|---|"]
            for m in ms:
                files = [f for f in (self.root / RAW / m["flight_id"]).iterdir() if f.is_file()]
                text.append(f"| [{m['flight_id']}]({page_id(m)}.md) | {when(m)} | {len(files)} | "
                            f"{size(sum(f.stat().st_size for f in files))} | {esc(m['header'].get('format', ''))} |")
                sections.append({"file": f"data/raw/{page_id(m)}"})
                self.raw_flight_page(m)
            text.append("")
        unprocessed = [d for d in find_flight_dirs(self.root) if d.name not in by_folder]
        if unprocessed:
            text += ["## Not processed", "", "Folders with no log the pipeline can read:", ""]
            text += [f"- [{d.name}]({tree(f'{RAW}/{d.name}')})" for d in unprocessed]
            text.append("")
        _, logs = load_wind_logs(self.root)
        text += ["## Wind drone", "", f"{len(logs)} log(s); see [wind drone](wind_drone.md).", ""]
        sections.append({"file": "data/raw/wind_drone"})
        self.wind_page(logs)
        _, hlogs = load_hwas_logs(self.root)
        text += ["## HWAS weather station", "", f"{len(hlogs)} file(s); see [HWAS weather station](hwas.md).", ""]
        sections.append({"file": "data/raw/hwas"})
        self.hwas_page(hlogs)
        self.write("data/raw/index.md", "\n".join(text))
        return {"file": "data/raw/index", "sections": sections}

    def raw_flight_page(self, m: dict) -> None:
        folder = self.root / RAW / m["flight_id"]
        rel = f"{RAW}/{m['flight_id']}/"
        text = [f"# {m['flight_id']}", "",
                f"{m['platform']['label']}, {when(m)}. Analysis: [experiment page](../../experiments/"
                f"{m['platform']['id']}/{page_id(m)}.md). Folder on [GitHub]({tree(rel)}).", "",
                "## Files", "", "| File | Size | |", "|---|---:|---|"]
        for f in sorted(folder.iterdir()):
            if f.is_file():
                note = " (read by the pipeline)" if f.name == Path(m["source"]["file"]).name else ""
                text.append(f"| {f.name}{note} | {size(f.stat().st_size)} | [view]({blob(rel + f.name)}) · "
                            f"[download]({raw_url(rel + f.name)}) |")
        text += ["", f"SHA-256 of `{Path(m['source']['file']).name}`: `{m['source']['sha256']}`", ""]
        hdr = {k: v for k, v in m["header"].items() if v not in ("", None)}
        if hdr:
            text += ["## Header", "", "| Key | Value |", "|---|---|"]
            text += [f"| {esc(k)} | {esc(v)} |" for k, v in hdr.items()]
            text.append("")
        ov = raw_overview(self.root / m["source"]["file"])
        text += ["## Columns", "",
                 f"{len(ov)} columns, {m['summary']['samples']} rows. Min / max are over the whole log; "
                 "“filled” is the share of rows with a value.", "",
                 f":::{{admonition}} Show all {len(ov)} columns", ":class: dropdown", "",
                 "| Column | Filled | Min | Max | First value |", "|---|---:|---:|---:|---|"]
        for r in ov:
            text.append(f"| `{esc(r['name'])}` | {r['filled']} | {esc(r['min'])} | {esc(r['max'])} | {esc(r['first'])} |")
        text.append(":::")
        self.write(f"data/raw/{page_id(m)}.md", "\n".join(text))

    def hwas_page(self, logs) -> None:
        d = f"{RAW}/{hwas_reader.DIR}"
        text = ["# HWAS weather station", "",
                "Wind speed, direction and gusts, air temperature, humidity and pressure from the USAFA mesonet's "
                f"High Wind Alert System (HWAS) station, one reading every 30 s ([{d}]({tree(d)})). The station reports "
                "in knots and °F (the export converts to SI), its timestamps are Mountain Time, and its pressure is "
                "sea-level-adjusted. The pipeline keeps the readings from a minute before to a minute after each "
                "flight.", "",
                f"| File | Start ({LOCAL_LABEL}) | End ({LOCAL_LABEL}) | Rows | Flights covered |", "|---|---|---|---:|---|"]
        for lg in logs:
            covered = [m for m in self.metas if m.get("hwas") and lg["name"] in m["hwas"].get("sources", [])]
            links = ", ".join(f"[{when(m)}](../../experiments/{m['platform']['id']}/{page_id(m)}.md)" for m in covered)
            text.append(f"| [{lg['name']}]({blob(d + '/' + lg['name'])}) | {fmt(lg['start_utc'], '%Y-%m-%d %H:%M %Z')} | "
                        f"{fmt(lg['end_utc'], '%H:%M %Z')} | {lg['rows']} | {links or 'none'} |")
        self.write("data/raw/hwas.md", "\n".join(text))

    def wind_page(self, logs) -> None:
        text = ["# Wind drone", "",
                "Wind speed, direction and temperature from the wind drone's sensor, recorded as ROS 2 bags "
                f"([raw_data/{WIND_DIR}]({tree(f'{RAW}/{WIND_DIR}')})). The pipeline reads the CSV export of each bag "
                "and cuts out the part that overlaps each flight, matching on GPS time.", "",
                f"| Log | Start ({LOCAL_LABEL}) | End ({LOCAL_LABEL}) | Rows | Recording | Flights covered |",
                "|---|---|---|---:|---|---|"]
        for lg in logs:
            covered = [m for m in self.metas if m.get("wind") and lg["name"] in m["wind"].get("sources", [])]
            links = ", ".join(f"[{when(m)}](../../experiments/{m['platform']['id']}/{page_id(m)}.md)" for m in covered)
            url = tree(f"{RAW}/{WIND_DIR}/{lg['name']}")
            closed = {True: "complete", False: "⚠️ not closed (file may be cut short)", None: "–"}[lg.get("bag_closed")]
            text.append(f"| [{lg['name']}]({url}) | {fmt(lg['start_utc'], '%Y-%m-%d %H:%M:%S %Z')} | "
                        f"{fmt(lg['end_utc'], '%H:%M:%S %Z')} | {lg['rows']} | {closed} | {links or 'none'} |")
        self.write("data/raw/wind_drone.md", "\n".join(text))

    # ---------------------------------------------------------------- processed + download
    def processed_page(self) -> None:
        text = ["# Processed data", "",
                "`processed/<flight>/` holds the cleaned data for each flight, generated from the raw log by the "
                "`nasauli` package:", "",
                "| File | Contents |", "|---|---|",
                "| `flight.parquet`, `flight.csv` | One row per logger sample: the standard columns below, then every other raw column under its original name |",
                "| `wind.parquet`, `wind.csv` | Wind-drone data for the flight's time window (only when a wind log overlaps) |",
                "| `hwas.parquet`, `hwas.csv` | HWAS weather-station readings around the flight (only when a station export covers it) |",
                "| `metadata.json` | Log header, summary numbers, data-quality findings, column units, source file and its SHA-256, pipeline version |",
                "",
                "`time_utc` is the logger's clock; `time_gps_utc` is that corrected to the autopilot's GPS time, and "
                "`time_local` is the same instant in Mountain Time (MST/MDT), which the website uses. Wind data is "
                "matched on GPS time. The standard columns have the same names and units for every drone and every log "
                "format; a column the log doesn't have is empty.", "",
                "## Loading", "", "```python", "import pandas as pd",
                f'df = pd.read_parquet("{raw_url("processed/Tarot450_20260925_165053Z/flight.parquet")}")',
                "```", "", "MATLAB: `parquetread(\"flight.parquet\")`. R: `arrow::read_parquet(\"flight.parquet\")`.", "",
                "## Position and altitude accuracy", "",
                f"**RTK base.** {RTK_BASE}", "",
                "**GPS fix types.** `gps_fix_type` records the receiver's solution for every sample, and each flight "
                "page shows how much of the flight was in each: RTK Fixed is centimeter-level relative to the base, "
                "RTK Float about 0.2–1 m, DGPS/SBAS about 0.5–2 m, and a plain 3D fix about 2–5 m. These are rules of "
                "thumb for open sky, and the spec-sheet figures are 50% (CEP) values, not bounds.", "",
                "**Horizontal position.** `lat_deg`/`lon_deg` (and `north_m`/`east_m`) are the autopilot's EKF "
                "estimate; `gps_lat_deg`/`gps_lon_deg` are the receiver's own solution. When hovering or on the "
                "ground the two agree to a few centimeters while the fix is RTK Fixed. At cruise speed they differ by "
                "1–2 m because the two MAVLink messages are sampled at slightly different times; use the "
                "`*_rx_UTC` columns when aligning with other sensors.", "",
                "**Altitude.** `alt_rel_m` is the EKF height above takeoff, which ArduPilot takes from the barometer; "
                "in these flights it drifts from the RTK height by about 1–2.5 m (each flight's notes give the "
                "numbers). `alt_msl_m` uses a reference the EKF sets at startup and can be off by several meters. For "
                "precise height use `gps_alt_m` while the fix is RTK Fixed.", "",
                "**Timing.** At 9 m/s the drone moves about 45 cm between 20 Hz samples, so time alignment usually "
                "matters more than the centimeter-level GPS error.", "",
                "## Flight columns", "", "| Column | Unit | Description |", "|---|---|---|"]
        text += [f"| `{k}` | {esc(u)} | {esc(d)} |" for k, (u, d) in FLIGHT_COLUMNS.items()]
        text += ["", "## Wind columns", "", "| Column | Unit | Description |", "|---|---|---|"]
        text += [f"| `{k}` | {esc(u)} | {esc(d)} |" for k, (u, d) in WIND_COLUMNS.items()]
        text += ["| `source` | - | Wind log the row came from |"]
        text += ["", "## HWAS weather-station columns", "",
                 "`hwas.parquet`/`hwas.csv`: the HWAS station's 30-second readings from a minute before to a minute "
                 "after the flight.", "", "| Column | Unit | Description |", "|---|---|---|"]
        text += [f"| `{k}` | {esc(u)} | {esc(d)} |" for k, (u, d) in HWAS_COLUMNS.items()]
        text += ["", "## Not used", "",
                 "The autopilot does not measure wind. Its `WND_*` messages (`ap_wind_*` columns) are an estimate that "
                 "stays at 0 / −180 in these logs, so they are kept only for completeness and are not used anywhere. "
                 "Wind comes from the wind drone and the HWAS station."]
        self.write("data/processed.md", "\n".join(text))

    def download_page(self, zips: dict[str, int]) -> None:
        total_raw = sum(f.stat().st_size for f in (self.root / RAW).rglob("*") if f.is_file())
        text = ["# Download", "",
                "## Everything", "",
                f"- [**Whole repository (zip)**]({REPO}/archive/refs/heads/{BRANCH}.zip): raw data, processed data and "
                f"code. Raw data alone is {size(total_raw)}.",
                f"- With git: `git clone {REPO}.git`", "",
                "## Processed data, all flights", ""]
        for name, label in [("nasauli_processed_parquet.zip", "Parquet + metadata"),
                            ("nasauli_processed_csv.zip", "CSV + metadata")]:
            if name in zips:
                text.append(f'- <a href="../downloads/{name}">{name}</a> ({label}, {size(zips[name])})')
        text += ["", "## By drone", "", "| Drone | Flights | Processed data |", "|---|---:|---|"]
        for pid in self.platform_order:
            name = f"{pid}_processed.zip"
            text.append(f"| {self.by_platform[pid][0]['platform']['label']} | {len(self.by_platform[pid])} | "
                        f'<a href="../downloads/{name}">{name}</a> ({size(zips.get(name, 0))}) |')
        text += ["", "## One flight", "",
                 "Each flight page under **Experiments** lists its files, and each **Raw data** page lists the raw logs.", "",
                 "## All flights in Python", "", "```python",
                 "import json, pathlib, pandas as pd",
                 "# after unzipping nasauli_processed_parquet.zip (or cloning the repository)",
                 "frames = []",
                 'for meta_path in pathlib.Path("processed").glob("*/metadata.json"):',
                 "    meta = json.loads(meta_path.read_text())",
                 '    df = pd.read_parquet(meta_path.with_name("flight.parquet"))',
                 '    df.insert(0, "flight_id", meta["flight_id"])',
                 '    df.insert(1, "platform", meta["platform"]["id"])',
                 "    frames.append(df)",
                 "flights = pd.concat(frames, ignore_index=True)", "```"]
        self.write("data/download.md", "\n".join(text))

    def overview(self) -> None:
        rows = ["| Drone | Flights | Airborne | Latest flight |", "|---|---:|---:|---|"]
        for pid in self.platform_order:
            ms = self.by_platform[pid]
            air = sum(m["summary"].get("airborne_s") or 0 for m in ms)
            rows.append(f"| [{ms[0]['platform']['label']}](experiments/{pid}/index.md) | {len(ms)} | "
                        f"{air / 60:.0f} min | [{when(ms[0])}](experiments/{pid}/{page_id(ms[0])}.md) |")
        known = set(self.by_platform)
        planned = [p["label"] for k, p in PLATFORMS.items() if k not in known]
        text = "\n".join(rows)
        if planned:
            text += "\n\nNo flights yet: " + ", ".join(planned) + "."
        self.write("_generated/overview.md", text)

    def tutorials(self) -> list[dict]:
        entries = []
        for nb in sorted((self.root / "notebooks").glob("*.ipynb")):
            (self.src / "tutorials").mkdir(parents=True, exist_ok=True)
            shutil.copy(nb, self.src / "tutorials" / nb.name)
            entries.append({"file": f"tutorials/{nb.stem}"})
        return entries

    def toc(self, exp, raw, tutorials) -> None:
        parts = [{"caption": "Experiments", "chapters": exp},
                 {"caption": "Data", "chapters": [raw, {"file": "data/processed"}, {"file": "data/download"}]}]
        if tutorials:
            parts.append({"caption": "Tutorials", "chapters": tutorials})
        parts.append({"caption": "About", "chapters": [{"file": "contributing"}]})
        toc = {"format": "jb-book", "root": "intro", "parts": parts}
        (self.src / "_toc.yml").write_text("# Generated by `python -m nasauli site`; do not edit.\n"
                                           + yaml.safe_dump(toc, sort_keys=False))


def raw_overview(path: Path, max_rows: int | None = None) -> list[dict]:
    """Per-column summary of a raw CSV (preamble lines starting with '#' skipped)."""
    skip = 0
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            if not line.startswith("#"):
                break
            skip += 1
    df = pd.read_csv(path, skiprows=skip, skipinitialspace=True, low_memory=False, nrows=max_rows)
    df.columns = [str(c).strip() for c in df.columns]
    out = []
    for c in df.columns:
        s = df[c]
        num = pd.to_numeric(s, errors="coerce")
        filled = s.notna() & (s.astype(str).str.strip() != "")
        first = s[filled].iloc[0] if filled.any() else ""
        if num.notna().sum() >= max(1, filled.sum() * 0.9):
            lo, hi = f"{num.min():.6g}", f"{num.max():.6g}"
        else:
            lo = hi = ""
        out.append({"name": c, "filled": f"{100 * filled.mean():.0f}%", "min": lo, "max": hi,
                    "first": str(first).strip()[:40]})
    return out


def make_zips(root: Path, metas: list[dict], out: Path) -> dict[str, int]:
    out.mkdir(parents=True, exist_ok=True)
    sizes = {}

    def build(name: str, members: list[Path]):
        p = out / name
        with zipfile.ZipFile(p, "w", zipfile.ZIP_DEFLATED) as z:
            for f in members:
                z.write(f, f.relative_to(root).as_posix())
        sizes[name] = p.stat().st_size

    all_files = sorted(f for m in metas for f in m["_dir"].iterdir() if f.is_file())
    build("nasauli_processed_parquet.zip", [f for f in all_files if f.suffix in (".parquet", ".json")])
    build("nasauli_processed_csv.zip", [f for f in all_files if f.suffix in (".csv", ".json")])
    for pid in sorted({m["platform"]["id"] for m in metas}):
        build(f"{pid}_processed.zip",
              sorted(f for m in metas if m["platform"]["id"] == pid for f in m["_dir"].iterdir() if f.is_file()))
    return sizes


def build_site(root: Path, out: Path, work: Path | None = None, run_sphinx: bool = True) -> Path:
    work = work or root / "_build" / "book"
    if work.exists():
        shutil.rmtree(work)
    shutil.copytree(root / "book", work)
    b = Book(root, work)
    exp = b.experiments()
    raw = b.raw_pages()
    b.processed_page()
    zip_dir = work / "_downloads_tmp"
    zips = make_zips(root, b.metas, zip_dir)
    b.download_page(zips)
    b.overview()
    b.toc(exp, raw, b.tutorials())
    if not run_sphinx:
        return work
    jb = shutil.which("jupyter-book") or str(Path(sys.executable).with_name("jupyter-book"))
    subprocess.run([jb, "build", str(work), "--keep-going"], check=True)
    html_dir = work / "_build" / "html"
    # interactive reports (embedded by the flight pages) and zip downloads
    (html_dir / "reports").mkdir(exist_ok=True)
    for m in b.metas:
        pre = f"{m['_prefix']}_" if m["_prefix"] else ""
        flight = pd.read_parquet(m["_dir"] / f"{pre}flight.parquet")
        wp = m["_dir"] / f"{pre}wind.parquet"
        wind = pd.read_parquet(wp) if wp.exists() else None
        hp = m["_dir"] / f"{pre}hwas.parquet"
        hw = pd.read_parquet(hp) if hp.exists() else None
        rel = f"{PROCESSED}/{m['flight_id']}/"
        downloads = {f: raw_url(rel + f) for f in m["files"]}
        back = f"../experiments/{m['platform']['id']}/{page_id(m)}.html"
        (html_dir / "reports" / f"{page_id(m)}.html").write_text(render(m, flight, wind, downloads, back, hw))
    shutil.copytree(zip_dir, html_dir / "downloads", dirs_exist_ok=True)
    (html_dir / ".nojekyll").write_text("")
    if out.exists():
        shutil.rmtree(out)
    shutil.copytree(html_dir, out)
    print(f"site: {out} ({len(b.metas)} flights, pipeline v{__version__})")
    return out
