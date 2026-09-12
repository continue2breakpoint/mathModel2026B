#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "$0")"
mkdir -p build
export TEXMFVAR="${TEXMFVAR:-/tmp/mathmodel-tex-cache}"
for pass in 1 2 3; do
  lualatex -interaction=nonstopmode -halt-on-error -file-line-error \
    -output-directory=build main.tex > "build/compile-${pass}.txt" 2>&1 || {
      tail -60 "build/compile-${pass}.txt"
      exit 1
    }
done
cp build/main.pdf paper.pdf
printf 'Generated paper/paper.pdf\n'
