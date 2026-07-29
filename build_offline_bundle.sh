#!/usr/bin/env bash
set -euo pipefail

root=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
python=${PYTHON:-python3}
output=${1:-"$root/dist/youbu-annotation-offline"}

case "$output" in
  /*) ;;
  *) output="$PWD/$output" ;;
esac

if [[ -e "$output" || -e "$output.tar.gz" ]]; then
  echo "输出路径或归档已存在，请换一个空路径：$output" >&2
  exit 2
fi

mkdir -p "$output/wheels"
"$python" -m pip wheel --wheel-dir "$output/wheels" "$root"
cp "$root/install_offline.sh" "$root/DEPLOYMENT.md" "$output/"

(
  cd "$output"
  sha256sum DEPLOYMENT.md install_offline.sh wheels/*.whl > SHA256SUMS
)

archive="$output.tar.gz"
tar -C "$(dirname -- "$output")" -czf "$archive" "$(basename -- "$output")"
echo "离线部署包：$archive"
