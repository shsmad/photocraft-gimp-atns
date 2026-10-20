#!/usr/bin/env bash
# GIMP-эталон для freqautosplit PoC: запускает Python-Fu payload headless.
# Usage: run_gimp_reference.sh <input> <outdir> [radius] [blur_type]
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ $# -lt 2 ]]; then
  echo "usage: $0 <input> <outdir> [radius] [blur_type]" >&2
  exit 2
fi

input="$(realpath "$1")"
outdir="$(realpath -m "$2")"
radius="${3:-10}"
blur_type="${4:-0}"

if ! command -v gimp-console >/dev/null 2>&1; then
  echo "gimp-console не найден" >&2
  exit 1
fi

mkdir -p "$outdir"
export FREQ_INPUT="$input"
export FREQ_OUTDIR="$outdir"
export FREQ_RADIUS="$radius"
export FREQ_BLUR_TYPE="$blur_type"
export FREQ_GIMP_VERSION="$(gimp --version 2>/dev/null | head -1 || true)"
export FREQ_PAYLOAD="$script_dir/gimp_freqsplit_reference.py"

echo "GIMP reference: input=$input out=$outdir radius=$radius blur_type=$blur_type"
exec gimp-console -i -n -s \
  --batch-interpreter=python-fu-eval \
  -b "import os; exec(open(os.environ['FREQ_PAYLOAD']).read())" \
  --quit
