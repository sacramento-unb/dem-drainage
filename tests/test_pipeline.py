from pathlib import Path
import shutil

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

from dem_drainage.pipeline import (
    DrainageConfig,
    DrainageError,
    DrainagePipeline,
    discover_dems,
)
from dem_drainage.vectorize import streams_to_geoparquet


class RecordingRunner:
    def __init__(self):
        self.calls = []
        self.executable = Path("/fake/whitebox_tools")
        self.dry_run = True
        self.on_tool_start = None

    def run(self, tool, arguments, *, cwd):
        self.calls.append((tool, list(arguments), cwd))


def config(tmp_path: Path, **changes) -> DrainageConfig:
    input_dir = tmp_path / "dems"
    input_dir.mkdir()
    values = dict(input_dir=input_dir, output_dir=tmp_path / "out")
    values.update(changes)
    return DrainageConfig(**values)


def write_dem(path: Path, *, west: float = 0, crs: str = "EPSG:31983") -> None:
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=4,
        height=4,
        count=1,
        dtype="float32",
        crs=crs,
        transform=from_origin(west, 4, 1, 1),
        nodata=-9999,
    ) as dataset:
        dataset.write(np.arange(16, dtype="float32").reshape(1, 4, 4))


def test_pipeline_uses_hack_order_and_vectorizes_ordered_raster(tmp_path):
    cfg = config(tmp_path)
    write_dem(cfg.input_dir / "tile.tif")
    runner = RecordingRunner()

    result = DrainagePipeline(cfg, runner).execute()[0]

    names = [call[0] for call in runner.calls]
    assert names == [
        "BreachDepressions",
        "D8Pointer",
        "D8FlowAccumulation",
        "ExtractStreams",
        "HackStreamOrder",
    ]
    assert result.drainage_geoparquet.name == "tile_drainage_hack_order.parquet"


def test_multiple_tiles_are_processed_independently(tmp_path):
    cfg = config(tmp_path)
    write_dem(cfg.input_dir / "tile_1.tif", west=0)
    write_dem(cfg.input_dir / "tile_2.tif", west=4)
    runner = RecordingRunner()

    results = DrainagePipeline(cfg, runner).execute()

    assert [result.source_dem.name for result in results] == ["tile_1.tif", "tile_2.tif"]
    assert [result.drainage_geoparquet.name for result in results] == [
        "tile_1_drainage_hack_order.parquet",
        "tile_2_drainage_hack_order.parquet",
    ]
    dem_args = [
        argument
        for tool, arguments, _ in runner.calls
        if tool == "BreachDepressions"
        for argument in arguments
        if argument.startswith("--dem=")
    ]
    assert dem_args == [
        f"--dem={cfg.input_dir / 'tile_1.tif'}",
        f"--dem={cfg.input_dir / 'tile_2.tif'}",
    ]
    assert all(
        "--fill_pits" in arguments
        for tool, arguments, _ in runner.calls
        if tool == "BreachDepressions"
    )
    assert not any(
        "mosaic" in argument
        for _, arguments, _ in runner.calls
        for argument in arguments
    )


def test_output_folder_is_excluded_from_recursive_discovery(tmp_path):
    cfg = config(tmp_path, recursive=True)
    write_dem(cfg.input_dir / "source.tif")
    cfg.output_dir.mkdir(parents=True)
    write_dem(cfg.output_dir / "old.tif")

    assert discover_dems(cfg) == [(cfg.input_dir / "source.tif").resolve()]


def test_invalid_threshold_is_rejected(tmp_path):
    cfg = config(tmp_path, threshold=0)
    write_dem(cfg.input_dir / "source.tif")

    with pytest.raises(DrainageError, match="limiar"):
        DrainagePipeline(cfg, RecordingRunner()).execute()


def test_zero_cells_without_nodata_are_normalized(tmp_path):
    cfg = config(tmp_path)
    source = cfg.input_dir / "source.tif"
    values = np.array([[0, 0, 0], [0, 12, 13], [0, 14, 15]], dtype="int16")
    with rasterio.open(
        source,
        "w",
        driver="GTiff",
        width=3,
        height=3,
        count=1,
        dtype="int16",
        crs="EPSG:31983",
        transform=from_origin(0, 3, 1, 1),
    ) as dataset:
        dataset.write(values, 1)

    runner = RecordingRunner()
    runner.dry_run = False
    prepared, temp_dir = DrainagePipeline(cfg, runner)._prepare_dem(source)
    try:
        with rasterio.open(prepared) as dataset:
            assert dataset.nodata == -32768
            assert dataset.read(1)[0, 0] == -32768
            assert dataset.read(1)[2, 2] == 15
    finally:
        if temp_dir:
            shutil.rmtree(temp_dir)


def test_area_threshold_requires_projected_crs(tmp_path):
    cfg = config(tmp_path, threshold_units="area")
    write_dem(cfg.input_dir / "source.tif", crs="EPSG:4326")

    with pytest.raises(DrainageError, match="SRC projetado"):
        DrainagePipeline(cfg, RecordingRunner()).execute()


def test_ordered_streams_are_written_directly_to_geoparquet(tmp_path):
    transform = from_origin(0, 5, 1, 1)
    ordered = np.zeros((5, 5), dtype="int16")
    pointer = np.zeros((5, 5), dtype="int16")
    ordered[:, 2] = 1
    pointer[:4, 2] = 8  # WhiteboxTools south is 8
    ordered[2, :2] = 2
    pointer[2, :2] = 2  # WhiteboxTools east is 2

    for name, values in (("ordered.tif", ordered), ("pointer.tif", pointer)):
        with rasterio.open(
            tmp_path / name,
            "w",
            driver="GTiff",
            width=5,
            height=5,
            count=1,
            dtype="int16",
            crs="EPSG:31983",
            transform=transform,
            nodata=-32768,
        ) as dataset:
            dataset.write(values, 1)

    output = tmp_path / "drainage.parquet"
    feature_count = streams_to_geoparquet(
        tmp_path / "ordered.tif", tmp_path / "pointer.tif", output
    )

    frame = __import__("geopandas").read_parquet(output)
    assert feature_count == len(frame) == 3
    assert set(frame.hack_order) == {1, 2}
    assert frame.crs.to_epsg() == 31983
    assert not list(tmp_path.glob("*.shp"))


def test_pipeline_end_to_end_with_whitebox_tools(tmp_path):
    """Integration test executing the full pipeline against real WhiteboxTools binary."""
    try:
        from dem_drainage.pipeline import WhiteboxRunner
        executable = WhiteboxRunner.discover()
    except DrainageError:
        pytest.skip("WhiteboxTools executable not available for integration test.")

    input_dir = tmp_path / "dems"
    input_dir.mkdir()
    output_dir = tmp_path / "out"
    output_dir.mkdir()

    # Create synthetic V-shaped valley DEM (30x30 cells, 10m res)
    x = np.arange(30)
    y = np.arange(30)
    xx, yy = np.meshgrid(x, y)
    elev = ((30 - yy) * 2.0 + np.abs(xx - 15) * 5.0).astype("float32")

    with rasterio.open(
        input_dir / "valley.tif",
        "w",
        driver="GTiff",
        width=30,
        height=30,
        count=1,
        dtype="float32",
        crs="EPSG:31983",
        transform=from_origin(500000, 7000000, 10, 10),
        nodata=-9999.0,
    ) as ds:
        ds.write(elev, 1)

    cfg = DrainageConfig(
        input_dir=input_dir,
        output_dir=output_dir,
        threshold=20,
        threshold_units="cells",
    )
    runner = WhiteboxRunner(executable, verbose=False)
    result = DrainagePipeline(cfg, runner).execute()[0]

    assert result.drainage_geoparquet.is_file()
    assert result.hack_order_raster.is_file()

    import geopandas as gpd
    gdf = gpd.read_parquet(result.drainage_geoparquet)
    assert len(gdf) >= 1
    assert set(gdf["hack_order"].unique()) == {1}
    assert gdf.crs.to_epsg() == 31983
    assert (gdf["length"] > 0).all()
