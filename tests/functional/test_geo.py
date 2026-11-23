"""Geospatial stack works end to end: GDAL/PROJ reprojection, shapely, geopandas I/O."""
import numpy as np
import pytest

pytestmark = pytest.mark.functional


def test_gdal_geotiff_write_and_reproject(tmp_path):
    gdal = pytest.importorskip("osgeo.gdal")
    from osgeo import osr

    gdal.UseExceptions()
    src_path = str(tmp_path / "src.tif")
    ds = gdal.GetDriverByName("GTiff").Create(src_path, 36, 18, 1, gdal.GDT_Float32)
    ds.SetGeoTransform((-180, 10, 0, 90, 0, -10))
    srs = osr.SpatialReference()
    srs.ImportFromEPSG(4326)  # needs PROJ's database (PROJ_DATA from activation)
    ds.SetProjection(srs.ExportToWkt())
    ds.GetRasterBand(1).WriteArray(np.arange(36 * 18, dtype="f4").reshape(18, 36) + 1)
    ds.FlushCache()
    ds = None

    out = gdal.Warp(str(tmp_path / "merc.tif"), src_path, dstSRS="EPSG:3857",
                    outputBounds=(-2.0e7, -1.0e7, 2.0e7, 1.0e7))
    target = osr.SpatialReference()
    target.ImportFromEPSG(3857)
    assert osr.SpatialReference(wkt=out.GetProjection()).IsSame(target), "output is not Web Mercator"
    data = out.GetRasterBand(1).ReadAsArray()
    assert data.size and data.max() > 0


def test_shapely_geometry():
    pytest.importorskip("shapely")
    from shapely.geometry import Point

    lens = Point(0, 0).buffer(1.0).intersection(Point(1, 0).buffer(1.0))
    exact = 2 * np.arccos(0.5) - 0.5 * np.sqrt(3)  # two unit circles, centers 1 apart
    assert abs(lens.area - exact) < 0.01


def test_geopandas_reproject_and_file_roundtrip(tmp_path):
    gpd = pytest.importorskip("geopandas")
    from shapely.geometry import Point

    gdf = gpd.GeoDataFrame({"name": ["a", "b"]},
                           geometry=[Point(0, 0).buffer(1), Point(10, 10).buffer(1)],
                           crs="EPSG:4326")
    assert gdf.to_crs("EPSG:3857").crs.to_epsg() == 3857
    path = tmp_path / "shapes.gpkg"
    gdf.to_file(path, driver="GPKG")
    back = gpd.read_file(path)
    assert list(back["name"]) == ["a", "b"]
    assert back.crs.to_epsg() == 4326
