#!/bin/bash
#
#   Install the test harness (pytest) OUTSIDE the env under test, once per Python minor
#   version. The env itself is never modified. Needs internet: run on a login node (or
#   any node with outbound access; Casper and Derecho compute nodes reached the internet
#   in the 2026-11-12 tests, but run it ahead of batch jobs anyway).
#
#   Usage: install.sh ENV_PREFIX
#   Env:   NPL_TESTKIT (default /glade/work/$USER/npl-testkit)
#          PYTEST_SPEC (default "pytest>=8,<10"); pyyaml is added for the suite's data files

set -euo pipefail
umask 022

prefix=$(readlink -f "${1:?usage: install.sh ENV_PREFIX}")
kit=${NPL_TESTKIT:-/glade/work/$USER/npl-testkit}
spec=${PYTEST_SPEC:-pytest>=8,<10}
py=$prefix/bin/python
pyver=$("$py" -c 'import sys; print("%d.%d" % sys.version_info[:2])')
site=$kit/site-py$pyver

if [[ -d $site ]]; then
    echo "Harness already installed for python $pyver: $site"
    exit 0
fi

mkdir -p "$kit"
# The env's own pip, with --target: installs into $site only, using this python's
# version markers (so e.g. tomli/exceptiongroup come along for older pythons).
"$py" -m pip install --quiet --no-compile --no-cache-dir --disable-pip-version-check \
    --target "$site.tmp" "$spec" pyyaml
mv "$site.tmp" "$site"
PYTHONPATH=$site "$py" -c 'import pytest; print("Installed pytest", pytest.__version__)'
echo "  for python $pyver at $site"
