# DEM drainage extraction with WhiteboxTools

This command-line tool accepts a folder of DEM tiles, builds one continuous elevation mosaic, extracts a D8 drainage network with [WhiteboxTools](https://github.com/jblindsay/whitebox-tools), and assigns **Hack stream order**:

- `1`: main drainage/trunk;
- `2`: tributaries that enter the main drainage;
- `3+`: successive tributary levels upstream.

The final GeoParquet stores that hierarchy in `hack_order` and is written directly from the ordered raster. This is intentionally Hack order, not Strahler order: Strahler assigns `1` to headwaters, which is the reverse of the requested convention.

## Pipeline

1. Validate that all DEM tiles are single-band rasters with the same CRS, resolution, and data type, then mosaic them in memory-bounded chunks with Rasterio/GDAL (nearest-neighbour resampling, so elevations are not smoothed).
2. Run `BreachDepressionsLeastCost` to cross embankments and other artificial barriers, filling any depressions that cannot be breached by default.
3. Create a D8 pointer and D8 flow accumulation raster.
4. Extract streams using a configurable contributing-area threshold.
5. Optionally remove short tributaries.
6. Run `HackStreamOrder` and vectorize the ordered raster directly to GeoParquet.

D8 is deliberate: WhiteboxTools requires a D8 pointer for `HackStreamOrder`. MD-infinity is useful for distributed hillslope flow but cannot define the single downstream tree required for hierarchical channel ordering.

## Install

Build or download the executable from the requested upstream project:

```bash
git clone https://github.com/jblindsay/whitebox-tools.git
cd whitebox-tools
python build.py
```

Then install this project:

```bash
python -m pip install -e .
```

Pass the resulting `whitebox_tools` executable with `--whitebox-exe`, put it on `PATH`, or set `WHITEBOX_TOOLS_EXE`.

## Run

The minimal invocation is:

```bash
dem-drainage /data/dem_tiles \
  --whitebox-exe /opt/whitebox-tools/WBT/whitebox_tools
```

## Run with Docker (Recommended)

You can run both the processing API and the React web studio with Docker Compose:

```bash
docker compose up -d
```

- Web Interface: [http://localhost:3000](http://localhost:3000)
- API Documentation & Health: [http://localhost:8000/docs](http://localhost:8000/docs)

To run the CLI directly via Docker on a local folder of DEMs:

```bash
docker compose run --rm cli /data/my_dems --output-dir /data/results
```

## Local web interface (Without Docker)

The React interface lets you choose a DEM folder, upload its GeoTIFF tiles to the local processor, monitor the run, choose any output, and visualize it on an interactive map.

Start the processing API in one terminal:

```bash
export WHITEBOX_TOOLS_EXE=/opt/whitebox-tools/WBT/whitebox_tools
.venv/bin/dem-drainage-web
```

Start the React interface in another terminal:

```bash
cd web
npm install
npm run dev
```

Open `http://localhost:3000`. Uploaded jobs are stored below the operating system temporary directory by default. Set `DEM_DRAINAGE_JOB_DIR` to choose a persistent job directory.

The default channel-initiation threshold is 10,000 cells. Calibrate it against known channels or imagery; there is no universal threshold. For a 10 cm DEM, for example, a 1,000 m² initiation area is 100,000 cells:

```bash
dem-drainage /data/dem_tiles \
  --threshold-cells 100000 \
  --breach-distance 1000 \
  --min-stream-length 5 \
  --output-dir /data/results \
  --whitebox-exe /opt/whitebox-tools/WBT/whitebox_tools
```

For a projected CRS, the threshold can instead be expressed in squared CRS map units:

```bash
dem-drainage /data/dem_tiles --threshold-area 1000
```

Useful operational options:

- `--recursive`: find DEMs below subfolders;
- `--pattern '*.tif'`: choose filename globs (repeatable);
- `--resume`: reuse already-created stage outputs after interruption;
- `--mosaic-memory-mb 256`: cap the RAM used by the chunked mosaic;
- `--no-fill-remaining`: leave depressions that exceed breach limits unresolved;
- `--clean`: remove intermediate rasters after a successful run;
- `--dry-run`: print the exact WhiteboxTools commands.

## Outputs

The output folder contains:

- `drainage_hack_order.parquet`: final GeoParquet drainage with `hack_order`, `length`, and `geometry` columns;
- `hack_order.tif`: ordered stream raster;
- `streams.tif` or `streams_pruned.tif`: extracted binary network;
- `flow_accumulation.tif`, `d8_pointer.tif`, `dem_breached.tif`, and `dem_mosaic.tif`: auditable intermediates;
- `run_manifest.json`: inputs, parameters, outputs, and hierarchy semantics.

## Scale and tiling caveat

The mosaic is written in bounded-memory chunks so folders with many source tiles do not require loading the whole corridor into RAM. Hydrological conditioning, accumulation, and Hack ordering are then run by WhiteboxTools on the continuous mosaic. This is necessary for a correct cross-tile contributing area and a globally consistent main channel. Raster CRS metadata is restored after every WhiteboxTools stage, and GeoParquet carries its CRS in-file.

Independently processing buffered tiles and clipping their cores is not hydrologically equivalent: upstream area is lost at every tile boundary and Hack orders can reset. For a centimetric, 2,000 km corridor, first partition the corridor into complete outlet catchments (not arbitrary rectangles), run this command once per catchment, and use sufficient boundary context. A truly continuous 400-billion-cell raster requires a distributed flow-routing engine; overlap alone does not make local WhiteboxTools accumulations globally correct.

The direct GeoParquet vectorization stage currently reads the ordered-stream and D8-pointer rasters into memory. Keep each catchment partition small enough for those two rasters plus working arrays to fit in RAM.

## Test

```bash
python -m pip install -e '.[test]'
pytest
```
