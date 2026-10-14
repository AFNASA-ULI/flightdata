# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository purpose

A public **data repository** for NASA ULI flight telemetry (Tarot 450 quadcopter so far; more drones to come),
plus a Python package (`nasauli/`) that turns the raw logs into cleaned data and a Jupyter Book website
published on GitHub Pages. See README.md for the user-facing description.

Logs are offloaded from an onboard Raspberry Pi that runs the logger and streams telemetry over MAVLink from
the flight controller. Wind data comes from a separate "wind drone" that records ROS 2 bags.

## Layout

```
raw_data/<any folders>/       original flight logs, any layout (e.g. raw_data/Tarot450/*.csv, or one folder per flight)
raw_data/wind_drone/          wind-drone bags (.mcap) + optional *_csv exports; one log can cover several flights
raw_data/hwas_data/           USAFA mesonet HWAS weather-station CSVs (30 s, Mountain Time); readers/hwas.py
processed/<flight_id>/        flight/wind/hwas .parquet+.csv, metadata.json; generated, never edit by hand
processed/skipped.json        logs that were not processed and why (e.g. no autopilot data)
nasauli/readers/              one module per raw format -> standard schema (nasauli/schema.py)
nasauli/platforms.py          drone registry (website groups experiments by drone)
nasauli/annotations.py        per-flight trims (keep_s on elapsed_s) and overrides of desired speeds / payload
nasauli/clean.py              local position, wind matching (on GPS-corrected time), airborne detection
nasauli/checks.py             summary numbers + data-quality findings
nasauli/pipeline.py           raw_data -> processed
nasauli/report.py             per-flight interactive Plotly page (embedded in the site via iframe)
nasauli/book.py               generates the Jupyter Book pages + _toc.yml, builds the site, zips downloads
book/                         hand-written site pages (intro.md, contributing.md), _config.yml, _static/
.github/workflows/build.yml   on push to main: process, commit processed/, build site, deploy Pages
```

## Commands

```bash
pip install -r requirements.txt -r requirements-site.txt
python -m nasauli process [raw_data/FLIGHT ...]   # all flights by default
python -m nasauli site --out _site                # Jupyter Book build; generated sources in _build/book
python -m nasauli repair-bag raw_data/wind_drone/<bag>   # rebuild metadata.yaml + <bag>_csv from the .mcap
```

There is no test suite; validate a change by running `process` on all flights and checking the output
(row counts, metadata checks), then `site`, and look at the pages.

## Conventions

- Don't modify files under `raw_data/` unless the user asks for a specific correction. Fixes normally belong
  in the reader or the cleaning step. `*_Fixed.csv` files are hand-edited copies and are not read.
- New logger format: add `nasauli/readers/<name>.py` (`NAME`, `sniff`, `read`) and register it in
  `readers/__init__.py`; keep the old readers. The v2 reader looks columns up by name.
- New drone: add it to `nasauli/platforms.py`.
- Flight IDs are `<drone short name>_<Mountain Time start>` (e.g. `Tarot450_20260925_104000`), independent of
  where the log sits under raw_data/. `process` (all flights) prunes processed/ folders no log produces.
- Logs with no autopilot data (logger had no MAVLink connection) are skipped and listed in skipped.json.
- Autopilot (Cube Orange+) DataFlash logs exported to one CSV per message (Mission Planner), kept zipped as
  received or as a folder, are read by `readers/ardupilot_csv.py` (used for 2026-10-07, when the Pi had no
  MAVLink). One log is split into flights at ARM/disarm (cut at the logging pause between flights); time is
  from GPS week/ms; ~6.7 Hz; BAT.Temp 0 = no sensor. A reader may return several flights (`read_sessions`).
- Wind-drone recordings are read from their `_csv` export when present, else straight from the .mcap
  (mcap + mcap-ros2-support, no ROS needed); duplicate rows across sources are dropped. `nasauli/bagtools.py`
  reproduces the team's ROS 2 CSV export byte-for-byte and the recorder's metadata.yaml (validated on the
  complete 2026-10-07/09 bags); it was used to rebuild wind_drone_20261009_100606 (unclosed, empty metadata).
- Desired speeds come from the header's `commanded_speed_ms`, payload from `notes` ("360g payload", "no payload");
  `annotations.py` overrides them and trims parts of a log the team says are not part of the test. A trim keeps
  the flight ID (from the raw log start), the original elapsed_s, and the home/RTK elevation from the whole log.
- Anything that changes processed outputs: bump `nasauli.__version__`.
- Generated site pages live only in `_build/`; edit `nasauli/book.py` or `book/`, never the output.
- Times: data files keep UTC columns plus `time_local`; everything shown to people (site, reports, check
  messages) is Mountain Time via `nasauli/localtime.py` (America/Denver).
- GPS accuracy wording: RTK is "cm-level vs. base" (base = Reach RS2+, not surveyed; `schema.RTK_BASE`).
  EKF `alt_rel_m` is barometric; takeoff elevation comes from RTK `gps_alt_m` before takeoff when available.
- Wind comes from the wind drone and HWAS only; the autopilot's `WND_*`/`ap_wind_*` estimate is not used. The
  logger-clock (chrony) warning was dropped on request; the GPS clock offset is still applied.
- Wind bags whose `.mcap` lacks the MCAP end marker were never closed cleanly (possibly cut short); the site
  and checks flag this.
- v2 raw logs start with `# key: value` lines ending in `# ---`. Column groups map to MAVLink messages
  (`HB_`, `ATT_`, `IMU_`, `GPS_`/`POS_`, `BAT_`, `VFR_`, `SRV*_us`, `VIB_`, `EKF_`, `BARO_`, `WND_`, ...), with
  `*_rx_UTC`/`*_Age_ms` giving receive time and staleness.
- Known logger quirks handled by the pipeline: `HB_Armed` mixes heartbeats from several components (the
  autopilot's have base_mode bit 0 set); the logger writes the magnetometer columns as `IMU_*mag_mT` although
  the values are milligauss (the committed raw files were relabeled `_mG`); the host clock is not
  chrony-synced (offset estimated from `SYST_UnixUsec`). v1 logs have several wrong unit labels, documented
  in `readers/legacy_v1.py`.
