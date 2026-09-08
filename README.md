# Cardiff, Built

An interactive 3D timeline of Cardiff's surviving building stock.

The published map joins:

- OS OpenMap Local building footprints (April 2026)
- GeoDS/CDRC domestic EPC construction-age attributes (2022 snapshot)
- Welsh Government LiDAR DSM and DTM height data
- the Cardiff local-authority boundary supplied through MapIt

Age data is banded and residential EPC coverage is incomplete. The map therefore keeps undated footprints visible only as subdued context. LiDAR height is available for 96.9% of footprints; the remainder use a conservative property-form estimate.

`scripts/build_data.py` performs the spatial join and writes static vector tiles into `dist/`. Its Python dependencies are `mapbox-vector-tile`, `mercantile`, `numpy`, `pyproj`, `rasterio`, `requests`, and `shapely`.

Contains OS data © Crown copyright and database right 2026. LiDAR © Welsh Government. EPC-derived attributes © GeoDS/CDRC.
