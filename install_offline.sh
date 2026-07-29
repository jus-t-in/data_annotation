#!/usr/bin/env bash
set -euo pipefail

root=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
python=${PYTHON:-python3}
venv=${1:-"$PWD/.venv"}

if [[ "$(uname -s)" != "Linux" ]]; then
  echo "当前版本仅支持 Linux" >&2
  exit 2
fi

"$python" -m venv "$venv"
"$venv/bin/python" -m pip install \
  --no-index \
  --find-links "$root/wheels" \
  youbu-annotation
"$venv/bin/python" -c "import matplotlib, numpy, pyqtgraph, scipy, PySide6, youbu_annotation"
"$venv/bin/youbu-annotation" --version
for command in youbu-auto-annotate youbu-annotation-aid youbu-visualize; do
  "$venv/bin/$command" --help >/dev/null
done

echo "安装完成：$venv"
echo "启动命令：$venv/bin/youbu-annotation"
