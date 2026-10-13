"""Command line: ``python -m nasauli process [raw_data/FLIGHT ...]`` and ``python -m nasauli site``."""

from __future__ import annotations

import argparse
from pathlib import Path

from . import __version__
from .pipeline import process_all, process_flight_dir


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="python -m nasauli", description=__doc__)
    ap.add_argument("--version", action="version", version=__version__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("process", help="raw_data/ -> processed/ (Parquet, CSV, metadata JSON)")
    p.add_argument("flight_dirs", nargs="*", type=Path, help="raw_data/<flight> folders (default: all)")
    p.add_argument("--root", type=Path, default=Path("."), help="repository root")
    r = sub.add_parser("repair-bag", help="rebuild metadata.yaml and the _csv export of a wind-drone bag folder")
    r.add_argument("bag_dirs", nargs="+", type=Path)
    r.add_argument("--force", action="store_true", help="overwrite an existing metadata.yaml / _csv folder")
    s = sub.add_parser("site", help="build the website (Jupyter Book) from processed/ and raw_data/")
    s.add_argument("--root", type=Path, default=Path("."))
    s.add_argument("--out", type=Path, default=Path("_site"))
    args = ap.parse_args(argv)

    if args.cmd == "process":
        if args.flight_dirs:
            for d in args.flight_dirs:
                print(d.name)
                process_flight_dir(d, args.root)
        else:
            process_all(args.root)
    elif args.cmd == "repair-bag":
        from .bagtools import repair

        for d in args.bag_dirs:
            repair(d, args.force)
    elif args.cmd == "site":
        from .book import build_site

        build_site(args.root, args.out)


if __name__ == "__main__":
    main()
