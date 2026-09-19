from __future__ import annotations

import io
import json
import os
import tempfile
import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import geopandas as gpd
import numpy as np
import rasterio
import uvicorn
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from PIL import Image
from rasterio.enums import Resampling
from rasterio.warp import transform_bounds

from .pipeline import DrainageConfig, DrainagePipeline, WhiteboxRunner


@dataclass
class Job:
    id: str
    input_dir: Path
    output_dir: Path
    status: str = "queued"
    stage: str = "Waiting to start"
    progress: int = 0
    file_count: int = 0
    total_bytes: int = 0
    error: str | None = None
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    completed_at: str | None = None

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "status": self.status,
            "stage": self.stage,
            "progress": self.progress,
            "file_count": self.file_count,
            "total_bytes": self.total_bytes,
            "error": self.error,
            "created_at": self.created_at,
            "completed_at": self.completed_at,
            "artifacts": _artifacts(self) if self.status == "complete" else [],
        }


_jobs: dict[str, Job] = {}
_jobs_lock = threading.Lock()
_job_root = Path(
    os.environ.get(
        "DEM_DRAINAGE_JOB_DIR",
        str(Path(tempfile.gettempdir()) / "dem-drainage-web-jobs"),
    )
).expanduser()

_cors_env = os.environ.get("DEM_DRAINAGE_CORS_ORIGINS", "").strip()
_allowed_origins = (
    [origin.strip() for origin in _cors_env.split(",") if origin.strip()]
    if _cors_env
    else ["http://localhost:3000", "http://127.0.0.1:3000", "http://0.0.0.0:3000", "*"]
)

app = FastAPI(title="DEM Drainage Studio API", version="0.2.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


def _whitebox_executable() -> Path | None:
    try:
        return WhiteboxRunner.discover()
    except Exception:
        return None


@app.get("/api/health")
def health() -> dict[str, Any]:
    executable = _whitebox_executable()
    return {
        "status": "ready" if executable else "missing_whitebox",
        "whitebox": str(executable) if executable else None,
        "job_root": str(_job_root),
    }


@app.post("/api/jobs", status_code=202)
async def create_job(
    files: list[UploadFile] = File(...),
    threshold: float = Form(10_000),
    threshold_units: str = Form("cells"),
    breach_distance: int = Form(100),
    min_stream_length: float | None = Form(None),
) -> dict[str, Any]:
    if threshold <= 0 or breach_distance <= 0:
        raise HTTPException(400, "Threshold and breach distance must be positive.")
    if threshold_units not in {"cells", "area"}:
        raise HTTPException(400, "Threshold units must be cells or area.")
    if _whitebox_executable() is None:
        raise HTTPException(
            503,
            "WhiteboxTools was not found. Set WHITEBOX_TOOLS_EXE before starting the API.",
        )

    job_id = uuid.uuid4().hex[:12]
    input_dir = _job_root / job_id / "input"
    output_dir = _job_root / job_id / "output"
    input_dir.mkdir(parents=True, exist_ok=False)
    output_dir.mkdir(parents=True, exist_ok=True)

    accepted = 0
    total_bytes = 0
    used_names: set[str] = set()
    try:
        for upload in files:
            name = Path(upload.filename or "").name
            if Path(name).suffix.lower() not in {".tif", ".tiff"}:
                continue
            if not name or name in used_names:
                raise HTTPException(400, f"Duplicate or invalid DEM filename: {name}")
            used_names.add(name)
            destination = input_dir / name
            with destination.open("wb") as sink:
                while chunk := await upload.read(1024 * 1024):
                    sink.write(chunk)
                    total_bytes += len(chunk)
            accepted += 1
    except Exception:
        for path in input_dir.glob("*"):
            path.unlink(missing_ok=True)
        raise
    finally:
        for upload in files:
            await upload.close()

    if accepted == 0:
        raise HTTPException(400, "The selected folder contains no GeoTIFF DEM files.")

    job = Job(
        id=job_id,
        input_dir=input_dir,
        output_dir=output_dir,
        file_count=accepted,
        total_bytes=total_bytes,
    )
    with _jobs_lock:
        _jobs[job_id] = job

    thread = threading.Thread(
        target=_run_job,
        args=(job, threshold, threshold_units, breach_distance, min_stream_length),
        daemon=True,
        name=f"drainage-{job_id}",
    )
    thread.start()
    return job.public()


def _update_job(job: Job, **changes: Any) -> None:
    with _jobs_lock:
        for key, value in changes.items():
            setattr(job, key, value)


def _run_job(
    job: Job,
    threshold: float,
    threshold_units: str,
    breach_distance: int,
    min_stream_length: float | None,
) -> None:
    stages = {
        "BreachDepressionsLeastCost": (18, "Breaching depressions"),
        "D8Pointer": (34, "Calculating flow direction"),
        "D8FlowAccumulation": (49, "Accumulating upstream flow"),
        "ExtractStreams": (66, "Extracting drainage cells"),
        "RemoveShortStreams": (75, "Pruning short tributaries"),
        "HackStreamOrder": (84, "Ordering the network"),
        "StreamsToGeoParquet": (94, "Writing GeoParquet"),
    }

    def stage_started(tool: str) -> None:
        progress, label = stages.get(tool, (10, tool))
        _update_job(job, stage=label, progress=progress)

    try:
        executable = WhiteboxRunner.discover()
        _update_job(job, status="processing", stage="Validating DEM tiles", progress=5)
        config = DrainageConfig(
            input_dir=job.input_dir,
            output_dir=job.output_dir,
            threshold=threshold,
            threshold_units=threshold_units,
            breach_distance=breach_distance,
            min_stream_length=min_stream_length,
            keep_intermediates=True,
        )
        runner = WhiteboxRunner(
            executable, verbose=False, on_tool_start=stage_started
        )
        DrainagePipeline(config, runner).execute()
        _update_job(
            job,
            status="complete",
            stage="Ready to explore",
            progress=100,
            completed_at=datetime.now(timezone.utc).isoformat(),
        )
    except Exception as exc:
        _update_job(
            job,
            status="failed",
            stage="Processing failed",
            error=str(exc),
            completed_at=datetime.now(timezone.utc).isoformat(),
        )


def _get_job(job_id: str) -> Job:
    with _jobs_lock:
        job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(404, "Job not found.")
    return job


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str) -> dict[str, Any]:
    return _get_job(job_id).public()


def _artifacts(job: Job) -> list[dict[str, Any]]:
    if not job.output_dir.is_dir():
        return []
    result = []
    for path in sorted(job.output_dir.iterdir(), key=lambda item: item.name):
        suffix = path.suffix.lower()
        if not path.is_file() or suffix not in {".tif", ".tiff", ".parquet", ".json"}:
            continue
        kind = "raster" if suffix in {".tif", ".tiff"} else "vector" if suffix == ".parquet" else "metadata"
        result.append(
            {
                "name": path.name,
                "kind": kind,
                "size": path.stat().st_size,
                "download_url": f"/api/jobs/{job.id}/artifacts/{path.name}",
                "preview_url": (
                    f"/api/jobs/{job.id}/preview/{path.name}"
                    if kind != "metadata"
                    else None
                ),
                "info_url": (
                    f"/api/jobs/{job.id}/preview/{path.name}/info"
                    if kind != "metadata"
                    else None
                ),
            }
        )
    return result


def _artifact_path(job: Job, name: str) -> Path:
    if not name or Path(name).name != name:
        raise HTTPException(400, "Invalid artifact name.")
    path = (job.output_dir / name).resolve()
    if path.parent != job.output_dir.resolve() or not path.is_file():
        raise HTTPException(404, "Artifact not found.")
    return path


@app.get("/api/jobs/{job_id}/artifacts/{name}")
def download_artifact(job_id: str, name: str) -> FileResponse:
    path = _artifact_path(_get_job(job_id), name)
    return FileResponse(path, filename=path.name)


def _wgs84_bounds(path: Path) -> list[list[float]]:
    if path.suffix.lower() == ".parquet":
        frame = gpd.read_parquet(path, columns=["geometry"])
        if frame.empty:
            return [[-1.0, -1.0], [1.0, 1.0]]
        bounds = frame.to_crs(4326).total_bounds
    else:
        with rasterio.open(path) as dataset:
            if dataset.crs is None:
                raise HTTPException(422, "Artifact has no CRS.")
            bounds = transform_bounds(dataset.crs, "EPSG:4326", *dataset.bounds)
    left, bottom, right, top = [float(value) for value in bounds]
    return [[bottom, left], [top, right]]


@app.get("/api/jobs/{job_id}/preview/{name}/info")
def preview_info(job_id: str, name: str) -> dict[str, Any]:
    path = _artifact_path(_get_job(job_id), name)
    suffix = path.suffix.lower()
    if suffix not in {".tif", ".tiff", ".parquet"}:
        raise HTTPException(415, "This artifact cannot be visualized.")
    return {
        "name": path.name,
        "kind": "vector" if suffix == ".parquet" else "raster",
        "bounds": _wgs84_bounds(path),
        "preview_url": f"/api/jobs/{job_id}/preview/{path.name}",
    }


@app.get("/api/jobs/{job_id}/preview/{name}")
def preview(job_id: str, name: str) -> Response:
    path = _artifact_path(_get_job(job_id), name)
    if path.suffix.lower() == ".parquet":
        return JSONResponse(_geoparquet_preview(path))
    if path.suffix.lower() in {".tif", ".tiff"}:
        return Response(_raster_preview(path), media_type="image/png")
    raise HTTPException(415, "This artifact cannot be visualized.")


def _geoparquet_preview(path: Path, limit: int = 50_000) -> dict[str, Any]:
    frame = gpd.read_parquet(path)
    total = len(frame)
    if "hack_order" in frame.columns:
        frame = frame.sort_values(["hack_order", "fid"])
    if total > limit:
        frame = frame.iloc[:limit]
    if frame.crs is None:
        raise HTTPException(422, "GeoParquet has no CRS metadata.")
    frame = frame.to_crs(4326)
    collection = json.loads(frame.to_json(drop_id=True))
    collection["preview"] = {"shown": len(frame), "total": total}
    return collection


def _raster_preview(path: Path, max_dimension: int = 1600) -> bytes:
    categorical = path.name.startswith(("hack_order", "streams", "d8_pointer"))
    with rasterio.open(path) as dataset:
        scale = min(1.0, max_dimension / max(dataset.width, dataset.height))
        width = max(1, round(dataset.width * scale))
        height = max(1, round(dataset.height * scale))
        data = dataset.read(
            1,
            out_shape=(height, width),
            masked=True,
            resampling=Resampling.nearest if categorical else Resampling.bilinear,
        )

    values = data.filled(np.nan).astype("float64")
    valid = np.isfinite(values)
    rgba = np.zeros((height, width, 4), dtype=np.uint8)
    if not valid.any():
        buffer = io.BytesIO()
        Image.fromarray(rgba, "RGBA").save(buffer, "PNG")
        return buffer.getvalue()

    if categorical:
        palette = np.array(
            [
                [31, 99, 123],
                [59, 139, 158],
                [95, 170, 177],
                [148, 194, 183],
                [229, 184, 107],
                [211, 132, 90],
                [175, 85, 82],
            ],
            dtype=np.uint8,
        )
        safe_values = np.where(valid, values, 1)
        indexes = np.maximum(np.rint(safe_values).astype("int64") - 1, 0) % len(palette)
        rgba[valid, :3] = palette[indexes[valid]]
    else:
        display = np.log1p(np.maximum(values, 0)) if "accumulation" in path.name else values
        low, high = np.nanpercentile(display[valid], [2, 98])
        if high <= low:
            high = low + 1
        normalized = np.where(valid, np.clip((display - low) / (high - low), 0, 1), 0)
        anchors = np.array(
            [[20, 37, 46], [41, 91, 103], [107, 151, 139], [207, 190, 143], [244, 236, 209]],
            dtype=float,
        )
        positions = normalized * (len(anchors) - 1)
        lower = np.floor(positions).astype(int)
        upper = np.minimum(lower + 1, len(anchors) - 1)
        fraction = (positions - lower)[..., None]
        colours = anchors[lower] * (1 - fraction) + anchors[upper] * fraction
        rgba[valid, :3] = colours[valid].astype(np.uint8)
    rgba[valid, 3] = 220

    buffer = io.BytesIO()
    Image.fromarray(rgba, "RGBA").save(buffer, "PNG", optimize=True)
    return buffer.getvalue()


def main() -> None:
    uvicorn.run(
        "dem_drainage.api:app",
        host=os.environ.get("DEM_DRAINAGE_HOST", "127.0.0.1"),
        port=int(os.environ.get("DEM_DRAINAGE_PORT", "8000")),
        reload=False,
    )


if __name__ == "__main__":
    main()
