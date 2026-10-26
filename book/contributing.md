# Adding data

## A new flight

1. Copy the logger's CSV into the drone's folder, for example `raw_data/Tarot450/`. Don't edit or re-save the
   file (opening and saving it in Excel changes it). Any folder layout under `raw_data/` works; each flight is
   named by its drone and Mountain Time start, e.g. `Tarot450_20261009_100644`.
   If the Pi logger missed the autopilot (no MAVLink connection), add the autopilot's own log instead: the
   Mission Planner CSV export of the .BIN log, zipped as it comes (e.g. `2026-10-07_11-58-05_log-491172.zip`),
   in the drone's folder. It is split into one flight per arm/disarm.
   Aurelia X6 recordings go in `raw_data/AureliaX6/`, one folder per recording: either the ROS 2 bag folder
   (with its `.mcap`) or its CSV export (one CSV per topic). If both are there, the CSV export is used.
2. Copy any new wind-drone recordings into `raw_data/wind_drone/`: the ROS 2 bag folder, plus its `_csv`
   export if you made one (the bag alone is enough). They are matched to flights by time, so one wind log can
   cover several flights.
   If a bag's `metadata.yaml` is missing or empty (the recorder was cut off), rebuild it and the CSV export with
   `python -m nasauli repair-bag raw_data/wind_drone/<bag_folder>`.
   HWAS weather-station exports (`hwas_wx_data_<date>_<time>.csv`) go in `raw_data/hwas_data/`.
3. **Dry runs and bench tests:** put `dry_run` in the folder or file name (e.g.
   `aurelia_x6_20261020_201136_csv_dry_run`). The website then labels the data as a dry run / bench test,
   adds `_dry_run` to its ID, shows no flight path, and doesn't match wind or weather data to it. Anything
   without `dry_run` in its name is treated as a real flight.
4. Commit and push to `main`.

The GitHub Action then processes every flight, commits the results to `processed/`, and rebuilds this
website. It takes a few minutes; the run shows under the repository's **Actions** tab.

## A new drone

Add an entry to `nasauli/platforms.py` with the drone's `platform` name as it appears in the log header
(for example `TAROT_450`), a display label, and the folder-name prefix used for its flights. The drone then
gets its own section under **Experiments**.

## A new log format

Each log format has a reader in `nasauli/readers/` that converts it to the standard columns. When the
logger's format changes, add a new reader and keep the old one, so older flights keep working. Bump
`nasauli.__version__` whenever processed outputs would change.

## Running it yourself

```bash
pip install -r requirements.txt -r requirements-site.txt
python -m nasauli process          # raw_data/ -> processed/
python -m nasauli site             # build this website into _site/
```
