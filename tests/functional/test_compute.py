"""Numerical libraries compute correct answers (numba JIT, scipy, scikit-learn)."""
import numpy as np
import pytest

pytestmark = pytest.mark.functional


@pytest.mark.timeout(300)
def test_numba_parallel_jit():
    numba = pytest.importorskip("numba")

    @numba.njit(parallel=True)
    def total(a):
        s = 0.0
        for i in numba.prange(a.size):
            s += a[i]
        return s

    a = np.arange(1_000_000, dtype="f8")
    assert total(a) == pytest.approx(a.sum())


def test_scipy_quadrature():
    integrate = pytest.importorskip("scipy.integrate")
    value, _ = integrate.quad(np.sin, 0, np.pi)
    assert value == pytest.approx(2.0)


def test_sklearn_linear_regression():
    linear_model = pytest.importorskip("sklearn.linear_model")
    rng = np.random.default_rng(0)
    x = rng.uniform(0, 10, (500, 1))
    y = 3.0 * x[:, 0] + 1.0 + rng.normal(0, 0.1, 500)
    fit = linear_model.LinearRegression().fit(x, y)
    assert fit.coef_[0] == pytest.approx(3.0, rel=1e-2)
    assert fit.intercept_ == pytest.approx(1.0, abs=0.05)
