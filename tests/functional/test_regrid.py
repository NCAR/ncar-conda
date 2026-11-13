"""xESMF/ESMF regridding is accurate (bilinear) and conserves the integral (conservative)."""
import numpy as np
import pytest

pytestmark = pytest.mark.functional
xe = pytest.importorskip("xesmf")


def analytic(lat, lon):
    """Smooth global test field."""
    return 2 + np.cos(np.deg2rad(lat)) ** 2 * np.cos(2 * np.deg2rad(lon))


def cell_areas(grid):
    """Unit-sphere cell areas of a regular lat/lon grid, from its cell bounds."""
    lat_b = np.deg2rad(grid["lat_b"].values[:, 0])
    lon_b = np.deg2rad(grid["lon_b"].values[0, :])
    return np.outer(np.diff(np.sin(lat_b)), np.diff(lon_b))


@pytest.fixture
def grids(in_tmp):
    src = xe.util.grid_global(2, 2)
    dst = xe.util.grid_global(5, 4)
    src["f"] = (("y", "x"), analytic(src["lat"].values, src["lon"].values))
    return src, dst


@pytest.mark.timeout(300)
def test_bilinear_matches_analytic_field(grids):
    src, dst = grids
    out = xe.Regridder(src, dst, "bilinear", periodic=True)(src["f"]).values
    expected = analytic(dst["lat"].values, dst["lon"].values)
    assert not np.isnan(out).any()
    assert np.abs(out - expected).max() < 0.02


@pytest.mark.timeout(300)
def test_conservative_preserves_global_integral(grids):
    src, dst = grids
    out = xe.Regridder(src, dst, "conservative")(src["f"]).values
    total_src = (src["f"].values * cell_areas(src)).sum()
    total_dst = (out * cell_areas(dst)).sum()
    # ESMF uses great-circle cell edges, so analytic lat/lon areas differ slightly (~1e-4);
    # broken weights are off by percent or more
    assert abs(total_dst - total_src) / total_src < 5e-3
