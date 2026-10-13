"""Processing pipeline for NASA ULI flight data.

raw_data/ (untouched logs)  ->  readers  ->  standard schema  ->  processed/ (Parquet, CSV, JSON, HTML)

The pipeline version is stamped into every output so a processed file can always be traced
back to the code that produced it. Bump it whenever outputs would change.
"""

__version__ = "1.4.0"
