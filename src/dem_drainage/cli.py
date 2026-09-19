from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .pipeline import DrainageConfig, DrainageError, DrainagePipeline, WhiteboxRunner


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dem-drainage",
        description=(
            "Extract a continuous drainage network from a folder of DEM tiles with "
            "WhiteboxTools and assign Hack order (main drainage = 1)."
        ),
    )
    parser.add_argument("dem_folder", type=Path, help="Folder containing DEM GeoTIFF tiles.")
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="Output folder (default: <dem_folder>/drainage_output).",
    )
    threshold = parser.add_mutually_exclusive_group()
    threshold.add_argument(
        "--threshold-cells",
        type=float,
        default=None,
        metavar="N",
        help="Channel-initiation threshold in accumulated cells (default: 10000).",
    )
    threshold.add_argument(
        "--threshold-area",
        type=float,
        default=None,
        metavar="AREA",
        help="Channel-initiation threshold in squared CRS map units.",
    )
    parser.add_argument(
        "--breach-distance",
        type=int,
        default=100,
        metavar="CELLS",
        help="Maximum least-cost breach search distance in cells (default: 100).",
    )
    parser.add_argument("--max-breach-cost", type=float)
    parser.add_argument(
        "--no-fill-remaining",
        action="store_true",
        help="Do not fill depressions that cannot be breached within the limits.",
    )
    parser.add_argument(
        "--min-stream-length",
        type=float,
        metavar="MAP_UNITS",
        help="Remove tributaries shorter than this map-unit length before ordering.",
    )
    parser.add_argument("--recursive", action="store_true", help="Search DEM subfolders.")
    parser.add_argument(
        "--pattern",
        action="append",
        dest="patterns",
        metavar="GLOB",
        help="Input filename glob; repeatable (default: *.tif and *.tiff).",
    )
    parser.add_argument(
        "--mosaic-memory-mb",
        type=int,
        default=256,
        help="Memory limit for the chunked raster mosaic (default: 256 MiB).",
    )
    parser.add_argument("--resume", action="store_true", help="Reuse existing stage outputs.")
    parser.add_argument(
        "--clean",
        action="store_true",
        help="Keep only the final vector, Hack-order raster, and manifest.",
    )
    parser.add_argument(
        "--whitebox-exe",
        type=Path,
        help="Path to the jblindsay/whitebox-tools executable.",
    )
    parser.add_argument(
        "--quiet-whitebox",
        action="store_true",
        help="Do not request verbose WhiteboxTools output.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print WhiteboxTools commands without executing them.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    input_dir = args.dem_folder.expanduser().resolve()
    output_dir = (
        args.output_dir.expanduser().resolve()
        if args.output_dir
        else input_dir / "drainage_output"
    )
    threshold = args.threshold_area
    units = "area"
    if threshold is None:
        threshold = args.threshold_cells if args.threshold_cells is not None else 10_000.0
        units = "cells"

    try:
        executable = WhiteboxRunner.discover(args.whitebox_exe)
        config = DrainageConfig(
            input_dir=input_dir,
            output_dir=output_dir,
            threshold=threshold,
            threshold_units=units,
            breach_distance=args.breach_distance,
            max_breach_cost=args.max_breach_cost,
            fill_remaining_depressions=not args.no_fill_remaining,
            min_stream_length=args.min_stream_length,
            recursive=args.recursive,
            patterns=tuple(args.patterns or ("*.tif", "*.tiff")),
            mosaic_memory_mb=args.mosaic_memory_mb,
            resume=args.resume,
            keep_intermediates=not args.clean,
        )
        result = DrainagePipeline(
            config,
            WhiteboxRunner(
                executable,
                verbose=not args.quiet_whitebox,
                dry_run=args.dry_run,
            ),
        ).execute()
    except DrainageError as exc:
        parser.exit(2, f"error: {exc}\n")

    print(f"Drainage GeoParquet: {result.drainage_geoparquet}")
    print(f"Hack-order raster: {result.hack_order_raster}")
    print("Hierarchy: hack_order 1 = main drainage; 2+ = successive tributary levels")
    return 0


if __name__ == "__main__":
    sys.exit(main())
