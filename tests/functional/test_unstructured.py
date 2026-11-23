"""Unstructured-grid analysis with uxarray: grid geometry, remapping/aggregation, UGRID file I/O.

All tests use an octahedron built in memory (6 nodes on the axes, 8 triangles covering the sphere),
so the expected values are exact by symmetry: every face has area pi/2 (total 4 pi on the unit sphere)
and a centroid at latitude +-arctan(1/sqrt 2) = +-35.26 degrees. No downloads.
"""
import numpy as np
import pytest

pytestmark = pytest.mark.functional

FACE_LAT = np.degrees(np.arctan(1 / np.sqrt(2)))


@pytest.fixture
def octahedron():
    ux = pytest.importorskip("uxarray")
    lon = np.array([0.0, 90.0, 180.0, -90.0, 0.0, 0.0])
    lat = np.array([0.0, 0.0, 0.0, 0.0, 90.0, -90.0])
    n, s = 4, 5                                   # north and south pole nodes
    faces = np.array([[0, 1, n], [1, 2, n], [2, 3, n], [3, 0, n],
                      [1, 0, s], [2, 1, s], [3, 2, s], [0, 3, s]])
    return ux, ux.Grid.from_topology(lon, lat, faces), lat


def test_uxarray_grid_geometry(octahedron):
    ux, grid, _ = octahedron
    assert (grid.n_node, grid.n_face) == (6, 8)
    areas = np.asarray(grid.face_areas)
    # uxarray integrates face areas by quadrature, so allow 0.1%
    np.testing.assert_allclose(areas, np.pi / 2, rtol=1e-3)
    np.testing.assert_allclose(areas.sum(), 4 * np.pi, rtol=1e-3)
    np.testing.assert_allclose(np.sort(np.asarray(grid.face_lat)), [-FACE_LAT] * 4 + [FACE_LAT] * 4, atol=1e-6)


def test_uxarray_remap_integrate_zonal_mean(octahedron):
    ux, grid, node_lat = octahedron
    # node -> face mean: each face has two equator nodes (0) and one pole (+-90)
    on_nodes = ux.UxDataArray(node_lat.copy(), uxgrid=grid, dims=["n_node"], name="node_lat")
    np.testing.assert_allclose(np.sort(np.asarray(on_nodes.topological_mean(destination="face"))),
                               [-30.0] * 4 + [30.0] * 4)
    # area-weighted integral of 1 is the sphere's area
    ones = ux.UxDataArray(np.ones(8), uxgrid=grid, dims=["n_face"], name="one")
    np.testing.assert_allclose(float(np.asarray(ones.integrate()).squeeze()), 4 * np.pi, rtol=1e-3)
    # zonal mean of +1 (north faces) / -1 (south faces): +1 and -1 away from the equator
    hemi = ux.UxDataArray(np.sign(np.asarray(grid.face_lat)), uxgrid=grid, dims=["n_face"], name="hemi")
    zonal = hemi.zonal_mean()
    lats, values = np.asarray(zonal[zonal.dims[0]], dtype=float), np.asarray(zonal)
    for target, expected in ((60.0, 1.0), (-60.0, -1.0)):
        assert np.isclose(lats, target).any(), f"zonal mean has no {target} latitude: {lats}"
        np.testing.assert_allclose(values[np.isclose(lats, target)], expected)


def test_uxarray_ugrid_file_roundtrip(octahedron, tmp_path):
    ux, grid, _ = octahedron
    import xarray as xr

    grid_path, data_path = tmp_path / "grid.nc", tmp_path / "data.nc"
    grid.to_xarray("ugrid").to_netcdf(grid_path)
    values = np.arange(8.0)
    xr.Dataset({"v": (("n_face",), values)}).to_netcdf(data_path)

    uxds = ux.open_dataset(grid_path, data_path)
    assert uxds.uxgrid.n_face == 8
    np.testing.assert_array_equal(np.asarray(uxds["v"]), values)
    np.testing.assert_allclose(np.asarray(uxds.uxgrid.face_areas).sum(), 4 * np.pi, rtol=1e-3)
