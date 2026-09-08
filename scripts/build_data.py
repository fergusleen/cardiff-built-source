#!/usr/bin/env python3
"""Build Cardiff construction-age vector tiles from open public datasets.

Sources:
  * OS OpenMap Local building polygons (April 2026), via Esri UK.
  * GeoDS/CDRC residential EPC attributes (2022 snapshot).
  * Cardiff boundary from MapIt (ONS/OS-derived boundary data).

The script downloads source data into /tmp/cardiff-built-cache, spatially joins
EPC address points to building polygons, and emits static Mapbox Vector Tiles.
"""

from __future__ import annotations

import concurrent.futures
import gzip
import json
import math
import os
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

import mapbox_vector_tile
import mercantile
import numpy as np
import rasterio
import requests
from mapbox_vector_tile.encoder import on_invalid_geometry_ignore
from pyproj import Transformer
from rasterio.enums import Resampling
from rasterio.transform import from_bounds as transform_from_bounds
from rasterio.windows import from_bounds as window_from_bounds
from shapely import contains_xy
from shapely.geometry import Point, box, mapping, shape
from shapely.ops import transform
from shapely.strtree import STRtree


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "dist"
CACHE = Path("/tmp/cardiff-built-cache")

BOUNDARY_URL = "https://mapit.mysociety.org/area/2639.geojson"
OS_QUERY_URL = (
    "https://services.arcgis.com/qHLhLQrcvEnxjtPr/arcgis/rest/services/"
    "OS_OpenMap_Local_Buildings/FeatureServer/1/query"
)
CDRC_CONFIG_URL = "https://mapmaker.geods.ac.uk/config.json"
CDRC_TILE_ROOT = (
    "https://vectortileservices-eu1.arcgis.com/N8pWnRJ1cgDCwHsq/arcgis/rest/services/"
    "uprn22_gb_epcdata_attr3/VectorTileServer/tile"
)
LIDAR_DSM_URL = "https://dmwproductionblob.blob.core.windows.net/cogs/lidar/wales_dsm_16bit_cog.tif"
LIDAR_DTM_URL = "https://dmwproductionblob.blob.core.windows.net/cogs/lidar/wales_dtm_16bit_cog.tif"

AGE_IDS = [1899, 1900, 1930, 1950, 1967, 1976, 1983, 1991, 1996, 2003, 2007, 2012, 2017]
AGE_LABELS = [
    "Before 1900",
    "1900–1929",
    "1930–1949",
    "1950–1966",
    "1967–1975",
    "1976–1982",
    "1983–1990",
    "1991–1995",
    "1996–2002",
    "2003–2006",
    "2007–2011",
    "2012–2016",
    "2017–2022",
]
AGE_RANK = {year: rank for rank, year in enumerate(AGE_IDS)}

WGS_TO_BNG = Transformer.from_crs(4326, 27700, always_xy=True)
BNG_TO_WGS = Transformer.from_crs(27700, 4326, always_xy=True)


def request_json(url: str, *, params=None, data=None, timeout=120):
    for attempt in range(5):
        try:
            if data is None:
                response = requests.get(url, params=params, timeout=timeout)
            else:
                response = requests.post(url, data=data, timeout=timeout)
            response.raise_for_status()
            payload = response.json()
            if payload.get("error"):
                raise RuntimeError(payload["error"])
            return payload
        except Exception:
            if attempt == 4:
                raise
    raise AssertionError("unreachable")


def load_boundary():
    cache_file = CACHE / "cardiff-boundary.geojson"
    if cache_file.exists():
        payload = json.loads(cache_file.read_text())
    else:
        payload = request_json(BOUNDARY_URL)
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps(payload, separators=(",", ":")))
    boundary_wgs = shape(payload)
    boundary_bng = transform(WGS_TO_BNG.transform, boundary_wgs)
    return payload, boundary_wgs, boundary_bng


def fetch_os_object_ids(boundary_wgs) -> list[int]:
    ids: set[int] = set()
    parts = list(boundary_wgs.geoms) if boundary_wgs.geom_type == "MultiPolygon" else [boundary_wgs]
    for part in parts:
        west, south, east, north = part.bounds
        payload = request_json(
            OS_QUERY_URL,
            params={
                "where": "1=1",
                "geometry": f"{west},{south},{east},{north}",
                "geometryType": "esriGeometryEnvelope",
                "inSR": "4326",
                "spatialRel": "esriSpatialRelIntersects",
                "returnIdsOnly": "true",
                "f": "json",
            },
        )
        ids.update(payload.get("objectIds", []))
    return sorted(ids)


def fetch_os_batch(batch: list[int]) -> list[dict]:
    cache_dir = CACHE / "os-buildings"
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file = cache_dir / f"{batch[0]}-{batch[-1]}.json.gz"
    if cache_file.exists():
        with gzip.open(cache_file, "rt") as handle:
            return json.load(handle).get("features", [])

    payload = request_json(
        OS_QUERY_URL,
        data={
            "objectIds": ",".join(str(value) for value in batch),
            "outFields": "OBJECTID",
            "returnGeometry": "true",
            "outSR": "27700",
            "f": "geojson",
        },
        timeout=180,
    )
    with gzip.open(cache_file, "wt", compresslevel=5) as handle:
        json.dump(payload, handle, separators=(",", ":"))
    return payload.get("features", [])


def load_os_buildings(boundary_wgs, boundary_bng):
    object_ids = fetch_os_object_ids(boundary_wgs)
    batches = [object_ids[i : i + 900] for i in range(0, len(object_ids), 900)]
    features: list[dict] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        for index, result in enumerate(pool.map(fetch_os_batch, batches), start=1):
            features.extend(result)
            if index % 20 == 0 or index == len(batches):
                print(f"OS footprints: {index}/{len(batches)} batches", flush=True)

    buildings = []
    object_ids_kept = []
    for feature in features:
        try:
            geom = shape(feature["geometry"])
        except Exception:
            continue
        if geom.is_empty or not boundary_bng.covers(geom.representative_point()):
            continue
        if not geom.is_valid:
            geom = geom.buffer(0)
        if geom.is_empty:
            continue
        buildings.append(geom)
        object_ids_kept.append(int(feature.get("id") or feature.get("properties", {}).get("OBJECTID", 0)))

    print(f"Cardiff footprints retained: {len(buildings):,}", flush=True)
    return buildings, object_ids_kept


def tile_point_to_wgs(tile, x: float, y: float, extent: int):
    bounds = mercantile.bounds(tile)
    lon = bounds.west + (x / extent) * (bounds.east - bounds.west)
    lat = bounds.south + (y / extent) * (bounds.north - bounds.south)
    return lon, lat


def fetch_cdrc_tile(args):
    tile, token = args
    cache_dir = CACHE / "epc-z13"
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file = cache_dir / f"{tile.x}-{tile.y}.pbf.gz"
    if cache_file.exists():
        raw = cache_file.read_bytes()
    else:
        url = f"{CDRC_TILE_ROOT}/{tile.z}/{tile.y}/{tile.x}.pbf"
        response = requests.get(url, params={"token": token}, timeout=120)
        if response.status_code == 404:
            cache_file.write_bytes(b"")
            return []
        response.raise_for_status()
        raw = response.content
        if not raw:
            return []
        cache_file.write_bytes(raw)
    if not raw:
        return []
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    decoded = mapbox_vector_tile.decode(raw)
    layer = decoded.get("uprn22_gb_epcdata_attr3")
    if not layer:
        return []
    extent = int(layer.get("extent", 4096))
    records = []
    for feature in layer.get("features", []):
        geometry = feature.get("geometry", {})
        coords = geometry.get("coordinates", [])
        if geometry.get("type") == "Point":
            coords = [coords]
        elif geometry.get("type") != "MultiPoint":
            continue
        properties = feature.get("properties", {})
        for x, y in coords:
            lon, lat = tile_point_to_wgs(tile, x, y, extent)
            records.append((lon, lat, properties))
    return records


def load_epc_points(boundary_wgs):
    config = request_json(CDRC_CONFIG_URL)
    token = config["arcgisTileAPIKey"]
    main_parts = list(boundary_wgs.geoms) if boundary_wgs.geom_type == "MultiPolygon" else [boundary_wgs]
    tile_set = set()
    for part in main_parts:
        west, south, east, north = part.bounds
        tile_set.update(mercantile.tiles(west, south, east, north, 13))
    tiles = sorted(tile_set, key=lambda value: (value.x, value.y))

    records = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        for index, result in enumerate(pool.map(fetch_cdrc_tile, [(tile, token) for tile in tiles]), start=1):
            records.extend(result)
            if index % 20 == 0 or index == len(tiles):
                print(f"EPC tiles: {index}/{len(tiles)}", flush=True)

    prepared = boundary_wgs
    grouped = defaultdict(list)
    for lon, lat, properties in records:
        if prepared.covers(Point(lon, lat)):
            east, north = WGS_TO_BNG.transform(lon, lat)
            grouped[(round(east, 1), round(north, 1))].append(properties)
    print(f"EPC address locations retained: {len(grouped):,}", flush=True)
    return grouped


def mode(values, fallback=-1):
    values = [value for value in values if value is not None and value != -1]
    if not values:
        return fallback
    return Counter(values).most_common(1)[0][0]


def join_epc(buildings, grouped_points):
    tree = STRtree(buildings)
    joined = defaultdict(list)
    matched_locations = 0
    for (east, north), records in grouped_points.items():
        point = Point(east, north)
        indices = tree.query(point, predicate="within")
        if len(indices):
            index = min((int(value) for value in indices), key=lambda value: buildings[value].area)
        else:
            nearest = tree.nearest(point)
            if nearest is None:
                continue
            index = int(nearest)
            if buildings[index].distance(point) > 14:
                continue
        joined[index].extend(records)
        matched_locations += 1
    print(f"EPC locations matched to footprints: {matched_locations:,}", flush=True)
    return joined


def estimate_height(footprint_area: float, records: list[dict]) -> int:
    if not records:
        if footprint_area < 180:
            return 7
        if footprint_area < 700:
            return 9
        if footprint_area < 2500:
            return 12
        if footprint_area < 8000:
            return 17
        return 23

    type_id = int(mode([record.get("type_id") for record in records], 0))
    floor_areas = [float(record["total_floo"]) for record in records if record.get("total_floo", 0) > 0]
    total_floor_area = sum(floor_areas)
    flat_like = 10 <= type_id < 20 or 30 <= type_id < 40
    bungalow = 0 < type_id < 10
    if flat_like:
        floors = max(2, math.ceil(total_floor_area / max(footprint_area * 0.78, 1)))
        return int(max(7, min(72, round(3.2 + floors * 2.9))))
    if bungalow:
        return 5
    if total_floor_area and footprint_area:
        floors = max(1, min(4, math.ceil(total_floor_area / max(footprint_area * 0.82, 1))))
        return int(round(3.0 + floors * 2.7))
    return 8


def fetch_lidar_grid(label: str, url: str, bounds, width: int, height: int):
    cache_file = CACHE / f"lidar-{label}-{width}x{height}.npy"
    if cache_file.exists():
        return np.load(cache_file)
    with rasterio.Env(
        GDAL_HTTP_UNSAFESSL="YES",
        GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR",
        GDAL_HTTP_MULTIRANGE="YES",
        CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".tif",
    ):
        with rasterio.open(url) as dataset:
            window = window_from_bounds(*bounds, transform=dataset.transform)
            grid = dataset.read(
                1,
                window=window,
                out_shape=(height, width),
                resampling=Resampling.nearest,
            )
    np.save(cache_file, grid)
    return grid


def load_lidar(buildings, resolution=4.0):
    minx = math.floor(min(geom.bounds[0] for geom in buildings) - 8)
    miny = math.floor(min(geom.bounds[1] for geom in buildings) - 8)
    maxx = math.ceil(max(geom.bounds[2] for geom in buildings) + 8)
    maxy = math.ceil(max(geom.bounds[3] for geom in buildings) + 8)
    bounds = (minx, miny, maxx, maxy)
    width = math.ceil((maxx - minx) / resolution)
    height = math.ceil((maxy - miny) / resolution)
    print(f"LiDAR grid: {width} x {height} at {resolution:.0f} m", flush=True)
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        dsm_future = pool.submit(fetch_lidar_grid, "dsm", LIDAR_DSM_URL, bounds, width, height)
        dtm_future = pool.submit(fetch_lidar_grid, "dtm", LIDAR_DTM_URL, bounds, width, height)
        dsm = dsm_future.result()
        dtm = dtm_future.result()
    heights = dsm.astype(np.int32) - dtm.astype(np.int32)
    grid_transform = transform_from_bounds(*bounds, width, height)
    return heights, grid_transform


def sample_lidar_height(geom, height_grid, grid_transform):
    minx, miny, maxx, maxy = geom.bounds
    inverse = ~grid_transform
    col0, row1 = inverse * (minx, miny)
    col1, row0 = inverse * (maxx, maxy)
    col_start = max(0, int(math.floor(min(col0, col1))))
    col_end = min(height_grid.shape[1] - 1, int(math.ceil(max(col0, col1))))
    row_start = max(0, int(math.floor(min(row0, row1))))
    row_end = min(height_grid.shape[0] - 1, int(math.ceil(max(row0, row1))))

    rows = np.arange(row_start, row_end + 1)
    cols = np.arange(col_start, col_end + 1)
    if not len(rows) or not len(cols):
        return None
    if len(rows) * len(cols) > 10000:
        stride = math.ceil(math.sqrt((len(rows) * len(cols)) / 10000))
        rows = rows[::stride]
        cols = cols[::stride]
    col_grid, row_grid = np.meshgrid(cols, rows)
    xs = grid_transform.c + (col_grid + 0.5) * grid_transform.a
    ys = grid_transform.f + (row_grid + 0.5) * grid_transform.e
    mask = contains_xy(geom, xs.ravel(), ys.ravel()).reshape(xs.shape)
    values = height_grid[np.ix_(rows, cols)][mask]
    values = values[(values >= 2) & (values <= 150)]

    if not len(values):
        point = geom.representative_point()
        col, row = inverse * (point.x, point.y)
        col = min(height_grid.shape[1] - 1, max(0, int(col)))
        row = min(height_grid.shape[0] - 1, max(0, int(row)))
        value = int(height_grid[row, col])
        return value if 2 <= value <= 150 else None
    return int(round(float(np.percentile(values, 65))))


def prepare_features(buildings, object_ids, joined, lidar):
    features = []
    counts = [0] * len(AGE_IDS)
    matched_properties = 0
    lidar_heights = 0
    height_grid, grid_transform = lidar
    for index, geom_bng in enumerate(buildings):
        records = joined.get(index, [])
        age_id = int(mode([record.get("age_id") for record in records], -1))
        rank = AGE_RANK.get(age_id, 99)
        if rank != 99:
            counts[rank] += 1
        matched_properties += len(records)
        type_id = int(mode([record.get("type_id") for record in records], 0))
        floor_values = [float(record["total_floo"]) for record in records if record.get("total_floo", 0) > 0]
        median_floor = int(round(statistics.median(floor_values))) if floor_values else 0
        area = max(1, int(round(geom_bng.area)))
        lidar_height = sample_lidar_height(geom_bng, height_grid, grid_transform)
        height = lidar_height if lidar_height is not None else estimate_height(geom_bng.area, records)
        height_source = 1 if lidar_height is not None else 0
        lidar_heights += height_source
        geom_wgs = transform(BNG_TO_WGS.transform, geom_bng)
        features.append(
            {
                "id": object_ids[index],
                "geometry": geom_wgs,
                "properties": {
                    "r": rank,
                    "y": age_id,
                    "h": height,
                    "u": len(records),
                    "t": type_id,
                    "f": median_floor,
                    "a": area,
                    "s": height_source,
                },
            }
        )

    cumulative = []
    running = 0
    for value in counts:
        running += value
        cumulative.append(running)
    return features, counts, cumulative, matched_properties, lidar_heights


def emit_vector_tiles(features, zooms=(11, 12, 13)):
    geoms = [feature["geometry"] for feature in features]
    tree = STRtree(geoms)
    west = min(geom.bounds[0] for geom in geoms)
    south = min(geom.bounds[1] for geom in geoms)
    east = max(geom.bounds[2] for geom in geoms)
    north = max(geom.bounds[3] for geom in geoms)
    tile_root = OUT / "tiles"

    total_files = 0
    total_bytes = 0
    for zoom in zooms:
        tiles = list(mercantile.tiles(west, south, east, north, zoom))
        for tile_index, tile in enumerate(tiles, start=1):
            bounds = mercantile.bounds(tile)
            tile_box = box(bounds.west, bounds.south, bounds.east, bounds.north)
            indices = tree.query(tile_box, predicate="intersects")
            tile_features = []
            for raw_index in indices:
                index = int(raw_index)
                clipped = geoms[index].intersection(tile_box)
                if clipped.is_empty:
                    continue
                if clipped.geom_type not in ("Polygon", "MultiPolygon"):
                    continue
                source = features[index]
                tile_features.append(
                    {
                        "id": source["id"],
                        "geometry": mapping(clipped),
                        "properties": source["properties"],
                    }
                )
            encoded = mapbox_vector_tile.encode(
                {"name": "buildings", "features": tile_features},
                default_options={
                    "quantize_bounds": (bounds.west, bounds.south, bounds.east, bounds.north),
                    "extents": 4096,
                    "on_invalid_geometry": on_invalid_geometry_ignore,
                },
            )
            target = tile_root / str(zoom) / str(tile.x) / f"{tile.y}.pbf"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(encoded)
            total_files += 1
            total_bytes += len(encoded)
        print(f"Vector tiles z{zoom}: {len(tiles)} candidate tiles", flush=True)
    print(f"Vector tiles emitted: {total_files} files, {total_bytes / 1_000_000:.1f} MB", flush=True)


def main():
    CACHE.mkdir(parents=True, exist_ok=True)
    (OUT / "data").mkdir(parents=True, exist_ok=True)
    (OUT / "tiles").mkdir(parents=True, exist_ok=True)

    boundary_payload, boundary_wgs, boundary_bng = load_boundary()
    buildings, object_ids = load_os_buildings(boundary_wgs, boundary_bng)
    grouped_points = load_epc_points(boundary_wgs)
    joined = join_epc(buildings, grouped_points)
    lidar = load_lidar(buildings)
    features, counts, cumulative, matched_properties, lidar_heights = prepare_features(buildings, object_ids, joined, lidar)
    emit_vector_tiles(features)

    dated = cumulative[-1]
    metadata = {
        "generated": "2026-09-08",
        "footprints": len(features),
        "datedBuildings": dated,
        "unknownBuildings": len(features) - dated,
        "matchedEpcRecords": matched_properties,
        "coveragePct": round(dated / len(features) * 100, 1),
        "lidarHeights": lidar_heights,
        "heightCoveragePct": round(lidar_heights / len(features) * 100, 1),
        "heightMethod": "Welsh Government LiDAR DSM minus DTM sampled within each footprint, with a property-form fallback",
        "ageIds": AGE_IDS,
        "ageLabels": AGE_LABELS,
        "counts": counts,
        "cumulative": cumulative,
        "tileMinZoom": 11,
        "tileMaxZoom": 13,
    }
    (OUT / "data" / "metadata.json").write_text(json.dumps(metadata, separators=(",", ":")))
    (OUT / "data" / "cardiff-boundary.geojson").write_text(
        json.dumps({"type": "Feature", "properties": {"name": "Cardiff"}, "geometry": boundary_payload}, separators=(",", ":"))
    )
    print(json.dumps(metadata, indent=2), flush=True)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
