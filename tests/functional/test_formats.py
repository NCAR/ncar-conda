"""Domain file formats read back what was written: GRIB (eccodes/cfgrib/pygrib), HDF4."""
import numpy as np
import pytest

pytestmark = pytest.mark.functional


@pytest.fixture
def grib_file(tmp_path):
    """A GRIB2 field written from eccodes' bundled sample (no external data needed)."""
    eccodes = pytest.importorskip("eccodes")
    gid = eccodes.codes_grib_new_from_samples("regular_ll_sfc_grib2")
    values = np.linspace(250.0, 300.0, eccodes.codes_get(gid, "numberOfValues"))
    eccodes.codes_set_values(gid, values)
    path = tmp_path / "sample.grib2"
    with open(path, "wb") as f:
        eccodes.codes_write(gid, f)
    eccodes.codes_release(gid)
    return path, values


def test_cfgrib_reads_grib(grib_file):
    xr = pytest.importorskip("xarray")
    pytest.importorskip("cfgrib")
    path, values = grib_file
    with xr.open_dataset(path, engine="cfgrib", backend_kwargs={"indexpath": ""}) as ds:
        field = ds[list(ds.data_vars)[0]].values.ravel()
    # sorted: independent of the reader's grid orientation; atol = GRIB packing precision
    np.testing.assert_allclose(np.sort(field), np.sort(values), atol=0.05)


def test_pygrib_reads_grib(grib_file):
    pygrib = pytest.importorskip("pygrib")
    path, values = grib_file
    grbs = pygrib.open(str(path))  # older pygrib has no context manager
    try:
        field = grbs.message(1).values.ravel()
    finally:
        grbs.close()
    np.testing.assert_allclose(np.sort(field), np.sort(values), atol=0.05)


def test_hdf4_roundtrip(tmp_path):
    pytest.importorskip("pyhdf.SD")
    from pyhdf.SD import SD, SDC

    data = np.arange(20, dtype="f4").reshape(4, 5)
    path = str(tmp_path / "roundtrip.hdf")
    f = SD(path, SDC.WRITE | SDC.CREATE)
    sds = f.create("v", SDC.FLOAT32, data.shape)
    sds[:] = data
    sds.endaccess()
    f.end()
    f = SD(path, SDC.READ)
    back = f.select("v")[:]
    f.end()
    np.testing.assert_array_equal(back, data)
