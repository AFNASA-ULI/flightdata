# Adding data

## A new flight

1. Copy the logger's CSV into the drone's folder, for example `raw_data/Tarot450/`. Don't edit or re-save the
   file (opening and saving it in Excel changes it). Any folder layout under `raw_data/` works; each flight is
   named by its drone and Mountain Time start, e.g. `Tarot450_20261009_100644`.
2. Copy any new wind-drone recordings into `raw_data/wind_drone/`: the ROS 2 bag folder, plus its `_csv`
   export if you made one (the bag alone is enough). They are matched to flights by time, so one wind log can
   cover several flights.
   HWAS weather-station exports (`hwas_wx_data_<date>_<time>.csv`) go in `raw_data/hwas_data/`.
3. Commit and push to `main`.

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
