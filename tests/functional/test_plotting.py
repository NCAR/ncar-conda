"""Plots render headlessly to a real image (matplotlib, cartopy)."""
import io

import numpy as np
import pytest

pytestmark = pytest.mark.functional
matplotlib = pytest.importorskip("matplotlib")
matplotlib.use("Agg")
import matplotlib.image  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402


def render(fig):
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=50)
    plt.close(fig)
    buf.seek(0)
    return matplotlib.image.imread(buf)


def test_matplotlib_renders_png():
    fig, ax = plt.subplots()
    ax.plot(np.arange(10), np.arange(10) ** 2)
    ax.set_title("npl test")
    img = render(fig)
    assert img.ndim == 3 and img.std() > 0, "blank image"


def test_cartopy_projected_plot_without_downloads(tmp_path):
    cartopy = pytest.importorskip("cartopy")
    import cartopy.crs as ccrs

    cartopy.config["data_dir"] = str(tmp_path)  # nothing may be fetched for this plot
    lon, lat = np.meshgrid(np.linspace(-180, 180, 73), np.linspace(-90, 90, 37))
    fig = plt.figure()
    ax = plt.axes(projection=ccrs.Robinson())
    ax.contourf(lon, lat, np.cos(np.deg2rad(lat)) * np.cos(np.deg2rad(lon)),
                transform=ccrs.PlateCarree())
    ax.set_global()
    img = render(fig)
    assert img.std() > 0, "blank image"
    assert not any(tmp_path.iterdir()), "plot without features should not download data"


@pytest.mark.network
@pytest.mark.timeout(300)
def test_cartopy_coastlines_download(tmp_path):
    cartopy = pytest.importorskip("cartopy")
    import cartopy.crs as ccrs

    cartopy.config["data_dir"] = str(tmp_path)
    fig = plt.figure()
    ax = plt.axes(projection=ccrs.PlateCarree())
    ax.coastlines("110m")
    render(fig)
    assert list(tmp_path.rglob("*coastline*.shp")), "Natural Earth coastlines not downloaded"
