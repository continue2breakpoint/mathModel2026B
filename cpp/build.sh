#!/usr/bin/env bash
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUT="${HERE}/geom_cpp$(python3-config --extension-suffix)"

PYBIND_INC="$(python3 -c 'import pybind11; print(pybind11.get_include())' 2>/dev/null || true)"
if [[ -z "${PYBIND_INC}" ]]; then
    PYBIND_INC="/usr/include/pybind11"
fi

g++ -O3 -std=c++17 -shared -fPIC -Wall -Wextra \
    $(python3-config --includes) \
    -I"${HERE}" \
    -I"${PYBIND_INC}" \
    "${HERE}/bindings.cpp" \
    -o "${OUT}"

echo "built ${OUT}"
