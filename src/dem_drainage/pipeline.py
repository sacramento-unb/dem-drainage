from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable, Sequence

import rasterio
from rasterio.crs import CRS
from rasterio.merge import merge

from .vectorize import VectorizationError, streams_to_geoparquet


class DrainageError(RuntimeError):
    """Raised when inputs are invalid or a WhiteboxTools operation fails."""


@dataclass(frozen=True)
class DrainageConfig:
    input_dir: Path
    output_dir: Path
    threshold: float = 10_000.0
    threshold_units: str = "cells"
    breach_distance: int = 100
    max_breach_cost: float | None = None
    fill_remaining_depressions: bool = True
    minimize_breach_distance: bool = True
    min_stream_length: float | None = None
    recursive: bool = False
    patterns: tuple[str, ...] = ("*.tif", "*.tiff")
    mosaic_memory_mb: int = 256
    resume: bool = False
    keep_intermediates: bool = True

    def validate(self) -> None:
        if not self.input_dir.is_dir():
            raise DrainageError(f"DEM folder does not exist: {self.input_dir}")
        if self.threshold <= 0:
            raise DrainageError("Stream threshold must be greater than zero.")
        if self.threshold_units not in {"cells", "area"}:
            raise DrainageError("threshold_units must be 'cells' or 'area'.")
        if self.breach_distance <= 0:
            raise DrainageError("breach_distance must be greater than zero.")
        if self.max_breach_cost is not None and self.max_breach_cost <= 0:
            raise DrainageError("max_breach_cost must be greater than zero.")
        if self.min_stream_length is not None and self.min_stream_length <= 0:
            raise DrainageError("min_stream_length must be greater than zero.")
        if self.mosaic_memory_mb < 16:
            raise DrainageError("mosaic_memory_mb must be at least 16.")


@dataclass(frozen=True)
class PipelineResult:
    drainage_geoparquet: Path
    hack_order_raster: Path
    streams_raster: Path
    flow_accumulation: Path
    d8_pointer: Path
    breached_dem: Path
    mosaic: Path
    manifest: Path


class WhiteboxRunner:
    """Small, dependency-free adapter for the official WhiteboxTools CLI."""

    def __init__(
        self,
        executable: Path,
        *,
        verbose: bool = True,
        dry_run: bool = False,
        on_tool_start: Callable[[str], None] | None = None,
    ):
        self.executable = executable
        self.verbose = verbose
        self.dry_run = dry_run
        self.on_tool_start = on_tool_start

    @staticmethod
    def discover(explicit: Path | None = None) -> Path:
        candidates: list[str] = []
        if explicit is not None:
            candidates.append(str(explicit.expanduser()))
        if os.environ.get("WHITEBOX_TOOLS_EXE"):
            candidates.append(os.environ["WHITEBOX_TOOLS_EXE"])
        discovered = shutil.which("whitebox_tools")
        if discovered:
            candidates.append(discovered)

        try:
            import whitebox  # type: ignore[import-not-found]
            wbt_dir = Path(whitebox.__file__).parent
            for subpath in ("WBT/whitebox_tools", "whitebox_tools"):
                candidate_path = wbt_dir / subpath
                if candidate_path.is_file() and os.access(candidate_path, os.X_OK):
                    candidates.append(str(candidate_path))
        except Exception:
            pass

        for candidate in candidates:
            path = Path(candidate).expanduser().resolve()
            if path.is_file() and os.access(path, os.X_OK):
                return path

        raise DrainageError(
            "WhiteboxTools executable not found. Pass --whitebox-exe, set "
            "WHITEBOX_TOOLS_EXE, or place whitebox_tools on PATH."
        )

    def run(self, tool: str, arguments: Sequence[str], *, cwd: Path) -> None:
        if self.on_tool_start is not None:
            self.on_tool_start(tool)
        command = [str(self.executable), f"--run={tool}", f"--wd={cwd}"]
        if self.verbose:
            command.append("--verbose")
        command.extend(arguments)
        print("[whitebox]", " ".join(command), flush=True)
        if self.dry_run:
            return
        try:
            subprocess.run(
                command,
                check=True,
                stdout=None if self.verbose else subprocess.PIPE,
                stderr=None if self.verbose else subprocess.STDOUT,
                text=not self.verbose,
            )
        except subprocess.CalledProcessError as exc:
            detail = f"\n{exc.stdout.strip()}" if exc.stdout else ""
            raise DrainageError(
                f"WhiteboxTools {tool} failed with exit code {exc.returncode}.{detail}"
            ) from exc


def discover_dems(config: DrainageConfig) -> list[Path]:
    """Return stable, unique DEM paths without ingesting our own output folder."""
    output = config.output_dir.resolve()
    found: dict[Path, None] = {}
    for pattern in config.patterns:
        iterator: Iterable[Path]
        iterator = config.input_dir.rglob(pattern) if config.recursive else config.input_dir.glob(pattern)
        for item in iterator:
            resolved = item.resolve()
            if not item.is_file() or output == resolved or output in resolved.parents:
                continue
            found[resolved] = None
    return sorted(found, key=lambda path: str(path).casefold())


def validate_dem_stack(inputs: Sequence[Path], threshold_units: str) -> CRS:
    """Validate the assumptions required by a lossless elevation mosaic."""
    reference_crs: CRS | None = None
    reference_dtype: str | None = None
    reference_resolution: tuple[float, float] | None = None
    for path in inputs:
        try:
            with rasterio.open(path) as dataset:
                if dataset.count != 1:
                    raise DrainageError(f"DEM must have exactly one band: {path}")
                if dataset.crs is None:
                    raise DrainageError(f"DEM has no coordinate reference system: {path}")
                if dataset.transform.b != 0 or dataset.transform.d != 0:
                    raise DrainageError(f"Rotated DEM grids are not supported: {path}")
                resolution = (abs(dataset.res[0]), abs(dataset.res[1]))
                if reference_crs is None:
                    reference_crs = dataset.crs
                    reference_dtype = dataset.dtypes[0]
                    reference_resolution = resolution
                elif dataset.crs != reference_crs:
                    raise DrainageError(f"DEM CRS differs from the first tile: {path}")
                elif dataset.dtypes[0] != reference_dtype:
                    raise DrainageError(f"DEM data type differs from the first tile: {path}")
                elif any(
                    abs(a - b) > max(abs(a), abs(b), 1.0) * 1e-9
                    for a, b in zip(resolution, reference_resolution or resolution)
                ):
                    raise DrainageError(f"DEM resolution differs from the first tile: {path}")
        except DrainageError:
            raise
        except rasterio.errors.RasterioError as exc:
            raise DrainageError(f"Cannot read DEM {path}: {exc}") from exc

    if reference_crs is None:
        raise DrainageError("No readable DEMs were supplied.")
    if threshold_units == "area" and not reference_crs.is_projected:
        raise DrainageError(
            "--threshold-area requires a projected CRS; use --threshold-cells for geographic DEMs."
        )
    return reference_crs


class DrainagePipeline:
    def __init__(self, config: DrainageConfig, runner: WhiteboxRunner):
        self.config = config
        self.runner = runner

    def execute(self) -> PipelineResult:
        cfg = self.config
        cfg.validate()
        dem_files = discover_dems(cfg)
        if not dem_files:
            joined = ", ".join(cfg.patterns)
            raise DrainageError(f"No DEM files matching {joined} found in {cfg.input_dir}.")

        source_crs = validate_dem_stack(dem_files, cfg.threshold_units)

        cfg.output_dir.mkdir(parents=True, exist_ok=True)
        work = cfg.output_dir.resolve()
        mosaic = work / "dem_mosaic.tif"
        breached = work / "dem_breached.tif"
        pointer = work / "d8_pointer.tif"
        accumulation = work / "flow_accumulation.tif"
        streams = work / "streams.tif"
        pruned_streams = work / "streams_pruned.tif"
        hack_order = work / "hack_order.tif"
        drainage = work / "drainage_hack_order.parquet"
        manifest = work / "run_manifest.json"

        hydrology_dem = self._create_mosaic(dem_files, mosaic)
        self._tool(
            "BreachDepressionsLeastCost",
            breached,
            [
                f"--dem={hydrology_dem}",
                f"--output={breached}",
                f"--dist={cfg.breach_distance}",
                *(
                    [f"--max_cost={cfg.max_breach_cost}"]
                    if cfg.max_breach_cost is not None
                    else []
                ),
                *(["--min_dist"] if cfg.minimize_breach_distance else []),
                *(["--fill"] if cfg.fill_remaining_depressions else []),
            ],
        )
        self._restore_raster_crs(breached, source_crs)
        self._tool(
            "D8Pointer",
            pointer,
            [f"--dem={breached}", f"--output={pointer}"],
        )
        self._restore_raster_crs(pointer, source_crs)
        out_type = "cells" if cfg.threshold_units == "cells" else "catchment area"
        self._tool(
            "D8FlowAccumulation",
            accumulation,
            [
                f"--input={pointer}",
                f"--output={accumulation}",
                f"--out_type={out_type}",
                "--pntr",
            ],
        )
        self._restore_raster_crs(accumulation, source_crs)
        self._tool(
            "ExtractStreams",
            streams,
            [
                f"--flow_accum={accumulation}",
                f"--output={streams}",
                f"--threshold={cfg.threshold}",
                "--zero_background",
            ],
        )
        self._restore_raster_crs(streams, source_crs)

        ordered_input = streams
        if cfg.min_stream_length is not None:
            self._tool(
                "RemoveShortStreams",
                pruned_streams,
                [
                    f"--d8_pntr={pointer}",
                    f"--streams={streams}",
                    f"--output={pruned_streams}",
                    f"--min_length={cfg.min_stream_length}",
                ],
            )
            self._restore_raster_crs(pruned_streams, source_crs)
            ordered_input = pruned_streams

        self._tool(
            "HackStreamOrder",
            hack_order,
            [
                f"--d8_pntr={pointer}",
                f"--streams={ordered_input}",
                f"--output={hack_order}",
                "--zero_background",
            ],
        )
        self._restore_raster_crs(hack_order, source_crs)
        if self.runner.on_tool_start is not None:
            self.runner.on_tool_start("StreamsToGeoParquet")
        if not self.runner.dry_run:
            try:
                streams_to_geoparquet(hack_order, pointer, drainage)
            except VectorizationError as exc:
                raise DrainageError(str(exc)) from exc

        result = PipelineResult(
            drainage_geoparquet=drainage,
            hack_order_raster=hack_order,
            streams_raster=ordered_input,
            flow_accumulation=accumulation,
            d8_pointer=pointer,
            breached_dem=breached,
            mosaic=hydrology_dem,
            manifest=manifest,
        )
        if not self.runner.dry_run:
            self._write_manifest(result, dem_files, source_crs)
            if not cfg.keep_intermediates:
                self._remove_intermediates(result)
        return result

    def _tool(self, tool: str, output: Path, arguments: Sequence[str]) -> None:
        if self.config.resume and output.exists():
            print(f"[resume] keeping {output}", flush=True)
            return
        self.runner.run(tool, arguments, cwd=self.config.output_dir.resolve())

    def _create_mosaic(self, inputs: Sequence[Path], output: Path) -> Path:
        if len(inputs) == 1:
            return inputs[0]
        if self.config.resume and output.exists():
            print(f"[resume] keeping {output}", flush=True)
            return output

        print(f"[mosaic] merging {len(inputs)} DEM tiles into {output}", flush=True)
        if self.runner.dry_run:
            print(
                f"[dry-run] rasterio merge ({self.config.mosaic_memory_mb} MiB limit)",
                flush=True,
            )
            return output
        try:
            merge(
                [str(path) for path in inputs],
                dst_path=output,
                resampling=rasterio.enums.Resampling.nearest,
                target_aligned_pixels=True,
                mem_limit=self.config.mosaic_memory_mb,
                dst_kwds={
                    "driver": "GTiff",
                    "compress": "deflate",
                    "tiled": True,
                    "blockxsize": 256,
                    "blockysize": 256,
                    "BIGTIFF": "YES",
                },
            )
        except (rasterio.errors.RasterioError, ValueError) as exc:
            raise DrainageError(f"Unable to mosaic DEM tiles: {exc}") from exc
        return output

    def _restore_raster_crs(self, path: Path, crs: CRS) -> None:
        if self.runner.dry_run or not path.exists():
            return
        try:
            with rasterio.open(path, "r+") as dataset:
                dataset.crs = crs
        except rasterio.errors.RasterioError as exc:
            raise DrainageError(f"Unable to restore CRS on {path}: {exc}") from exc

    def _write_manifest(
        self, result: PipelineResult, inputs: Sequence[Path], source_crs: CRS
    ) -> None:
        config_data = asdict(self.config)
        config_data["input_dir"] = str(self.config.input_dir.resolve())
        config_data["output_dir"] = str(self.config.output_dir.resolve())
        config_data["patterns"] = list(self.config.patterns)
        payload = {
            "created_at": datetime.now(timezone.utc).isoformat(),
            "whitebox_executable": str(self.runner.executable),
            "crs": source_crs.to_string(),
            "inputs": [str(path) for path in inputs],
            "config": config_data,
            "outputs": {key: str(value) for key, value in asdict(result).items()},
            "hierarchy": {
                "method": "Hack stream order",
                "vector_field": "hack_order",
                "meaning": "1=main drainage; 2=direct tributaries; 3+=successive tributary levels",
            },
        }
        result.manifest.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    def _remove_intermediates(self, result: PipelineResult) -> None:
        for name in (
            "dem_mosaic.tif",
            "dem_breached.tif",
            "d8_pointer.tif",
            "flow_accumulation.tif",
            "streams.tif",
            "streams_pruned.tif",
        ):
            path = self.config.output_dir / name
            if path.resolve().is_relative_to(self.config.output_dir.resolve()):
                path.unlink(missing_ok=True)
