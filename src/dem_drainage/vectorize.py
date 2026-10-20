from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
from shapely.geometry import LineString


class VectorizationError(RuntimeError):
    """Erro quando rasters de canais ordenados não podem virar GeoParquet."""


# Convenção nativa do ponteiro D8 do WhiteboxTools (sentido horário a partir do nordeste):
# 128: North (-1, 0)
# 1: North-East (-1, 1)
# 2: East (0, 1)
# 4: South-East (1, 1)
# 8: South (1, 0)
# 16: South-West (1, -1)
# 32: West (0, -1)
# 64: North-West (-1, -1)
_DIRECTIONS = {
    128: (-1, 0),
    1: (-1, 1),
    2: (0, 1),
    4: (1, 1),
    8: (1, 0),
    16: (1, -1),
    32: (0, -1),
    64: (-1, -1),
}


def _shift_slices(
    rows: int, cols: int, dy: int, dx: int
) -> tuple[tuple[slice, slice], tuple[slice, slice]]:
    source_rows = slice(max(0, -dy), min(rows, rows - dy))
    source_cols = slice(max(0, -dx), min(cols, cols - dx))
    target_rows = slice(max(0, dy), min(rows, rows + dy))
    target_cols = slice(max(0, dx), min(cols, cols + dx))
    return (source_rows, source_cols), (target_rows, target_cols)


def streams_to_geoparquet(
    ordered_streams: Path, d8_pointer: Path, output: Path
) -> int:
    """Converte canais D8 ordenados por Hack diretamente para GeoParquet.

    Divide os trechos seguindo diretamente a topologia dos canais e grava
    GeoParquet sem criar um formato vetorial intermediário.
    """
    try:
        with rasterio.open(ordered_streams) as stream_src, rasterio.open(
            d8_pointer
        ) as pointer_src:
            if stream_src.shape != pointer_src.shape:
                raise VectorizationError("Os rasters de canais e direção D8 não estão alinhados.")
            if stream_src.transform != pointer_src.transform:
                raise VectorizationError("As transformações dos rasters de canais e direção D8 são diferentes.")
            streams = stream_src.read(1)
            pointer = pointer_src.read(1)
            transform = stream_src.transform
            crs = stream_src.crs
    except rasterio.errors.RasterioError as exc:
        raise VectorizationError(f"Não foi possível ler os rasters de canais ordenados: {exc}") from exc

    stream_mask = np.isfinite(streams) & (streams > 0)
    rows, cols = streams.shape
    inflowing = np.full((rows, cols), -1, dtype=np.int8)
    inflowing[stream_mask] = 0

    for value, (dy, dx) in _DIRECTIONS.items():
        source_slice, target_slice = _shift_slices(rows, cols, dy, dx)
        valid = (
            stream_mask[source_slice]
            & (pointer[source_slice] == value)
            & stream_mask[target_slice]
        )
        target_counts = inflowing[target_slice]
        target_counts[valid] += 1

    stack = [tuple(int(v) for v in cell) for cell in np.argwhere(inflowing == 0)]
    geometries: list[LineString] = []
    orders: list[int] = []

    def centre(row: int, col: int) -> tuple[float, float]:
        x, y = transform @ (col + 0.5, row + 0.5)
        return float(x), float(y)

    while stack:
        row, col = stack.pop()
        if not (0 <= row < rows and 0 <= col < cols) or inflowing[row, col] == -1:
            continue

        order = int(round(float(streams[row, col])))
        inflowing[row, col] = -1
        points: list[tuple[float, float]] = []
        previous_direction: int | None = None

        while 0 <= row < rows and 0 <= col < cols:
            raw_direction = pointer[row, col]
            direction = int(raw_direction) if np.isfinite(raw_direction) else 0
            point_added = direction != previous_direction
            if point_added:
                points.append(centre(row, col))
                previous_direction = direction

            if direction not in _DIRECTIONS or not stream_mask[row, col]:
                if not point_added:
                    points.append(centre(row, col))
                break

            dy, dx = _DIRECTIONS[direction]
            next_row, next_col = row + dy, col + dx
            if not (0 <= next_row < rows and 0 <= next_col < cols):
                break

            next_order = (
                int(round(float(streams[next_row, next_col])))
                if stream_mask[next_row, next_col]
                else 0
            )
            row, col = next_row, next_col
            if inflowing[row, col] != 1 or next_order != order:
                points.append(centre(row, col))
                if stream_mask[row, col]:
                    stack.append((row, col))
                break

        if len(points) > 1:
            if points[-1] == points[-2]:
                points.pop()
            if len(points) > 1:
                geometries.append(LineString(points))
                orders.append(order)

    if geometries:
        frame = gpd.GeoDataFrame(
            {
                "fid": np.arange(1, len(geometries) + 1, dtype=np.int64),
                "hack_order": np.asarray(orders, dtype=np.int16),
            },
            geometry=geometries,
            crs=crs,
        )
        frame["length"] = frame.geometry.length
    else:
        frame = gpd.GeoDataFrame(
            {
                "fid": pd.Series(dtype="int64"),
                "hack_order": pd.Series(dtype="int16"),
                "length": pd.Series(dtype="float64"),
            },
            geometry=gpd.GeoSeries([], crs=crs),
            crs=crs,
        )

    output.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(output, index=False, compression="zstd")
    return len(frame)
