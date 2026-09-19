from pathlib import Path

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

    result = DrainagePipeline(cfg, runner).execute()

    names = [call[0] for call in runner.calls]
    assert names == [
        "BreachDepressionsLeastCost",
        "D8Pointer",
        "D8FlowAccumulation",
        "ExtractStreams",
        "HackStreamOrder",
    ]
    assert result.drainage_geoparquet.name == "drainage_hack_order.parquet"


def test_multiple_tiles_are_mosaiced_without_losing_edge_pixels_or_crs(tmp_path):
    cfg = config(tmp_path)
    write_dem(cfg.input_dir / "tile_1.tif", west=0)
    write_dem(cfg.input_dir / "tile_2.tif", west=4)
    runner = RecordingRunner()
    runner.dry_run = False

    result_path = cfg.output_dir / "dem_mosaic.tif"
    cfg.output_dir.mkdir()
    result = DrainagePipeline(cfg, runner)._create_mosaic(
        [cfg.input_dir / "tile_1.tif", cfg.input_dir / "tile_2.tif"], result_path
    )

    with rasterio.open(result) as mosaic:
        assert (mosaic.width, mosaic.height) == (8, 4)
        assert mosaic.crs == rasterio.CRS.from_epsg(31983)


def test_output_folder_is_excluded_from_recursive_discovery(tmp_path):
    cfg = config(tmp_path, recursive=True)
    write_dem(cfg.input_dir / "source.tif")
    cfg.output_dir.mkdir(parents=True)
    write_dem(cfg.output_dir / "old.tif")

    assert discover_dems(cfg) == [(cfg.input_dir / "source.tif").resolve()]


def test_invalid_threshold_is_rejected(tmp_path):
    cfg = config(tmp_path, threshold=0)
    write_dem(cfg.input_dir / "source.tif")

    with pytest.raises(DrainageError, match="threshold"):
        DrainagePipeline(cfg, RecordingRunner()).execute()


def test_area_threshold_requires_projected_crs(tmp_path):
    cfg = config(tmp_path, threshold_units="area")
    write_dem(cfg.input_dir / "source.tif", crs="EPSG:4326")

    with pytest.raises(DrainageError, match="projected CRS"):
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
        breach_distance=10,
    )
    runner = WhiteboxRunner(executable, verbose=False)
    result = DrainagePipeline(cfg, runner).execute()

    assert result.drainage_geoparquet.is_file()
    assert result.hack_order_raster.is_file()

    import geopandas as gpd
    gdf = gpd.read_parquet(result.drainage_geoparquet)
    assert len(gdf) >= 1
    assert set(gdf["hack_order"].unique()) == {1}
    assert gdf.crs.to_epsg() == 31983
    assert (gdf["length"] > 0).all()
