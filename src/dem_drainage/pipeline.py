from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable, Sequence

import numpy as np
import rasterio
from rasterio.crs import CRS

from .vectorize import VectorizationError, streams_to_geoparquet


class DrainageError(RuntimeError):
    """Erro para entradas inválidas ou falhas em operações do WhiteboxTools."""


class EmptyDEMError(DrainageError):
    """Erro para arquivos sem células de elevação utilizáveis."""


@dataclass(frozen=True)
class DrainageConfig:
    input_dir: Path
    output_dir: Path
    threshold: float = 10_000.0
    threshold_units: str = "cells"
    fill_remaining_depressions: bool = True
    min_stream_length: float | None = None
    recursive: bool = False
    patterns: tuple[str, ...] = ("*.tif", "*.tiff")
    resume: bool = False
    keep_intermediates: bool = True

    def validate(self) -> None:
        if not self.input_dir.is_dir():
            raise DrainageError(f"A pasta dos DEMs não existe: {self.input_dir}")
        if self.threshold <= 0:
            raise DrainageError("O limiar dos canais deve ser maior que zero.")
        if self.threshold_units not in {"cells", "area"}:
            raise DrainageError("A unidade do limiar deve ser 'cells' ou 'area'.")
        if self.min_stream_length is not None and self.min_stream_length <= 0:
            raise DrainageError("O comprimento mínimo dos canais deve ser maior que zero.")


@dataclass(frozen=True)
class PipelineResult:
    drainage_geoparquet: Path
    hack_order_raster: Path
    streams_raster: Path
    flow_accumulation: Path
    d8_pointer: Path
    breached_dem: Path
    source_dem: Path
    manifest: Path


class WhiteboxRunner:
    """Adaptador pequeno e sem dependências para a CLI oficial do WhiteboxTools."""

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
            "Executável do WhiteboxTools não encontrado. Informe --whitebox-exe, defina "
            "WHITEBOX_TOOLS_EXE ou coloque whitebox_tools no PATH."
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
                f"WhiteboxTools {tool} falhou com código de saída {exc.returncode}.{detail}"
            ) from exc


def discover_dems(config: DrainageConfig) -> list[Path]:
    """Retorna caminhos DEM estáveis e únicos sem incluir a própria pasta de saída."""
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


def validate_dem(path: Path, threshold_units: str) -> CRS:
    """Valida um arquivo antes de executar o processamento."""
    try:
        with rasterio.open(path) as dataset:
            if dataset.count != 1:
                raise DrainageError(f"O DEM deve ter exatamente uma banda: {path}")
            if dataset.crs is None:
                raise DrainageError(f"O DEM não possui sistema de referência de coordenadas: {path}")
            if dataset.transform.b != 0 or dataset.transform.d != 0:
                raise DrainageError(f"Grades DEM rotacionadas não são suportadas: {path}")
            crs = dataset.crs
    except DrainageError:
        raise
    except rasterio.errors.RasterioError as exc:
        raise DrainageError(f"Não foi possível ler o DEM {path}: {exc}") from exc

    if threshold_units == "area" and not crs.is_projected:
        raise DrainageError(
            "--threshold-area exige um SRC projetado; use --threshold-cells para DEMs geográficos."
        )
    return crs


class DrainagePipeline:
    def __init__(self, config: DrainageConfig, runner: WhiteboxRunner):
        self.config = config
        self.runner = runner

    def execute(self) -> list[PipelineResult]:
        cfg = self.config
        cfg.validate()
        dem_files = discover_dems(cfg)
        if not dem_files:
            joined = ", ".join(cfg.patterns)
            raise DrainageError(f"Nenhum arquivo DEM correspondente a {joined} foi encontrado em {cfg.input_dir}.")

        cfg.output_dir.mkdir(parents=True, exist_ok=True)
        results = []
        for dem_file in dem_files:
            try:
                results.append(self._execute_tile(dem_file))
            except EmptyDEMError as exc:
                print(f"[ignorado] {exc}", flush=True)
        if not results:
            raise DrainageError("Nenhum arquivo DEM com dados de elevação utilizáveis foi encontrado.")
        return results

    def _execute_tile(self, dem_file: Path) -> PipelineResult:
        cfg = self.config
        source_crs = validate_dem(dem_file, cfg.threshold_units)
        work = cfg.output_dir.resolve()
        prefix = self._tile_prefix(dem_file)
        output = lambda suffix: work / f"{prefix}_{suffix}"
        breached = output("dem_breached.tif")
        pointer = output("d8_pointer.tif")
        accumulation = output("flow_accumulation.tif")
        streams = output("streams.tif")
        pruned_streams = output("streams_pruned.tif")
        hack_order = output("hack_order.tif")
        drainage = output("drainage_hack_order.parquet")
        manifest = output("run_manifest.json")

        processing_dem, temp_dir = self._prepare_dem(dem_file)
        try:
            self._tool(
                "BreachDepressions",
                breached,
                [
                    f"--dem={processing_dem}",
                    f"--output={breached}",
                    *(["--fill_pits"] if cfg.fill_remaining_depressions else []),
                ],
            )
        finally:
            if temp_dir is not None:
                shutil.rmtree(temp_dir, ignore_errors=True)
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
            source_dem=dem_file,
            manifest=manifest,
        )
        if not self.runner.dry_run:
            self._write_manifest(result, [dem_file], source_crs)
            if not cfg.keep_intermediates:
                self._remove_intermediates(result)
        return result

    def _tile_prefix(self, dem_file: Path) -> str:
        relative = dem_file.resolve().relative_to(self.config.input_dir.resolve())
        raw = "__".join(relative.with_suffix("").parts)
        return re.sub(r"[^A-Za-z0-9._-]+", "_", raw)

    def _prepare_dem(self, dem_file: Path) -> tuple[Path, Path | None]:
        if self.runner.dry_run:
            return dem_file, None
        with rasterio.open(dem_file) as dataset:
            if dataset.nodata is not None or dataset.dtypes[0][0] not in "iu":
                return dem_file, None
            data = dataset.read(1)
            valid = data != 0
            if not valid.any():
                raise EmptyDEMError(f"O arquivo DEM não contém dados de elevação: {dem_file}")
            if valid.all():
                return dem_file, None

            dtype = np.dtype(dataset.dtypes[0])
            nodata = np.iinfo(dtype).min if dtype.kind == "i" else 0
            temp_dir = Path(tempfile.mkdtemp(prefix="dem-drainage-nodata-"))
            prepared = temp_dir / dem_file.name
            profile = dataset.profile.copy()
            profile.update(nodata=nodata)
            with rasterio.open(prepared, "w", **profile) as output:
                output.write(np.where(valid, data, nodata), 1)
        return prepared, temp_dir

    def _tool(self, tool: str, output: Path, arguments: Sequence[str]) -> None:
        if self.config.resume and output.exists():
            print(f"[retomar] mantendo {output}", flush=True)
            return
        self.runner.run(tool, arguments, cwd=self.config.output_dir.resolve())

    def _restore_raster_crs(self, path: Path, crs: CRS) -> None:
        if self.runner.dry_run or not path.exists():
            return
        try:
            with rasterio.open(path, "r+") as dataset:
                dataset.crs = crs
        except rasterio.errors.RasterioError as exc:
                raise DrainageError(f"Não foi possível restaurar o SRC em {path}: {exc}") from exc

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
                "method": "ordem de canais Hack",
                "vector_field": "hack_order",
                "meaning": "1=drenagem principal; 2=tributários diretos; 3+=níveis sucessivos de tributários",
            },
        }
        result.manifest.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    def _remove_intermediates(self, result: PipelineResult) -> None:
        for path in (
            result.breached_dem,
            result.d8_pointer,
            result.flow_accumulation,
            result.streams_raster,
        ):
            if path.resolve().is_relative_to(self.config.output_dir.resolve()):
                path.unlink(missing_ok=True)
