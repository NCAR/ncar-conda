"""Data written with the env's I/O stack reads back identically (netCDF, HDF5, zarr)."""
import numpy as np
import pytest

pytestmark = pytest.mark.functional
xr = pytest.importorskip("xarray")


def sample_dataset():
    """Small dataset with a non-standard (noleap) calendar, as in climate model output."""
    cftime = pytest.importorskip("cftime")
    rng = np.random.default_rng(0)
    times = cftime.num2date(np.arange(4), "days since 2000-01-01", calendar="noleap")
    data = rng.standard_normal((4, 6, 8)).astype("f4")
    return xr.Dataset(
        {"t": (("time", "lat", "lon"), data, {"units": "K"})},
        coords={"time": times, "lat": np.linspace(-75, 75, 6), "lon": np.arange(8) * 45.0},
    )


@pytest.mark.parametrize("engine", ["netcdf4", "h5netcdf"])
def test_netcdf_roundtrip(engine, tmp_path):
    pytest.importorskip({"netcdf4": "netCDF4", "h5netcdf": "h5netcdf"}[engine])
    ds = sample_dataset()
    path = tmp_path / f"roundtrip_{engine}.nc"
    encoding = {"t": {"zlib": True, "complevel": 4}} if engine == "netcdf4" else {}
    ds.to_netcdf(path, engine=engine, encoding=encoding)
    with xr.open_dataset(path, engine=engine) as back:
        np.testing.assert_array_equal(back["t"].values, ds["t"].values)
        assert back["t"].attrs["units"] == "K"
        assert back["time"].values[0].calendar == "noleap"


def test_zarr_roundtrip(tmp_path):
    pytest.importorskip("zarr")
    ds = sample_dataset()
    store = tmp_path / "roundtrip.zarr"
    ds.to_zarr(store, mode="w")
    back = xr.open_zarr(store)
    np.testing.assert_array_equal(back["t"].values, ds["t"].values)
    assert back["t"].chunks is not None, "open_zarr should return dask-backed arrays"


def test_blosc_compression_roundtrip():
    numcodecs = pytest.importorskip("numcodecs")
    codec = numcodecs.Blosc(cname="zstd", clevel=5, shuffle=numcodecs.Blosc.SHUFFLE)
    data = np.arange(200_000, dtype="f8")
    encoded = codec.encode(data)
    assert len(encoded) < data.nbytes / 4, "blosc/zstd should compress a ramp strongly"
    np.testing.assert_array_equal(np.frombuffer(codec.decode(encoded), dtype="f8"), data)


def test_h5py_compressed_roundtrip(tmp_path):
    h5py = pytest.importorskip("h5py")
    data = np.arange(10_000, dtype="i4").reshape(100, 100)
    with h5py.File(tmp_path / "roundtrip.h5", "w") as f:
        f.create_dataset("v", data=data, compression="gzip", chunks=(10, 100))
    with h5py.File(tmp_path / "roundtrip.h5", "r") as f:
        np.testing.assert_array_equal(f["v"][:], data)


def test_dask_chunked_reduction_matches_numpy(tmp_path):
    pytest.importorskip("dask")
    pytest.importorskip("netCDF4")
    ds = sample_dataset()
    path = tmp_path / "chunked.nc"
    ds.to_netcdf(path)
    with xr.open_dataset(path, chunks={"time": 1}) as lazy:
        assert lazy["t"].chunks is not None
        result = lazy["t"].mean("time").compute()
    np.testing.assert_allclose(result.values, ds["t"].values.mean(axis=0), rtol=1e-6)
