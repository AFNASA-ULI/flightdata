# NASA ULI Flight Data

Flight test data from the NASA University Leadership Initiative (ULI) data collection. Each flight comes
with interactive analysis plots, a list of data-quality findings, the raw logs exactly as recorded, and
cleaned data in Parquet and CSV.

::::{grid} 1 1 3 3
:gutter: 3

:::{grid-item-card} 📈 Experiments
:link: experiments/index
:link-type: doc

Flights by drone and date, with interactive plots of trajectory, attitude, power, vibration, EKF health
and wind.
:::

:::{grid-item-card} 🗂️ Raw data
:link: data/raw/index
:link-type: doc

The logs as they came off each drone: file listings, header information, a column overview, and
downloads.
:::

:::{grid-item-card} ⬇️ Download
:link: data/download
:link-type: doc

Everything at once, all cleaned data in one zip, or the data for a single drone.
:::
::::

## Collection so far

```{include} _generated/overview.md
```

## How the data is organized

- **Raw data** is never edited. Each flight has a folder in
  [`raw_data/`](https://github.com/AFNASA-ULI/flightdata/tree/main/raw_data). Wind-drone logs
  (`raw_data/wind_drone/`) and the USAFA mesonet HWAS weather station (`raw_data/hwas_data/`) are matched to
  flights by time.
- **Processed data** (`processed/<flight>/`) is generated from the raw data by the `nasauli` Python package: a
  standard set of columns with fixed names and units, the wind data for that flight, and a `metadata.json`
  with the summary and data-quality findings. See [Processed data](data/processed.md).
- This website is rebuilt automatically whenever new data is pushed to the repository. See
  [Adding data](contributing.md).
