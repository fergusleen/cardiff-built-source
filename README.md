# The built history of Cardiff, Wales - http://github.com/fergusleen/cardiff-built-source Sept 2026.

An interactive 3D timeline of Cardiff's surviving building stock.

Live map: https://fergusleen.github.io/cardiff-built-source/

## Preview and deployment

Run `python3 -m http.server 8000 --directory dist` from the project folder and open http://localhost:8000. Opening `dist/index.html` directly does not allow the browser to fetch the map data.

GitHub Actions publishes the contents of `dist/` to GitHub Pages on each push to `main`. To refresh the data, run `scripts/build_data.py` with its Python dependencies installed, then commit and push the updated `dist/` files. Deployment uses the committed data and does not rerun the data build.

The published map joins:

- OS OpenMap Local building footprints (April 2026)
- GeoDS/CDRC domestic EPC construction-age attributes (2022 snapshot)
- Welsh Government LiDAR DSM and DTM height data
- the Cardiff local-authority boundary supplied through MapIt

Age data is banded and residential EPC coverage is incomplete. The map therefore keeps undated footprints visible only as subdued context. LiDAR height is available for 96.9% of footprints; the remainder use a conservative property-form estimate.

`scripts/build_data.py` performs the spatial join and writes static vector tiles into `dist/`. Its Python dependencies are `mapbox-vector-tile`, `mercantile`, `numpy`, `pyproj`, `rasterio`, `requests`, and `shapely`.

Contains OS data © Crown copyright and database right 2026. LiDAR © Welsh Government. EPC-derived attributes © GeoDS/CDRC.
