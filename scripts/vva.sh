#!/bin/sh
set -eu

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

if command -v python3 >/dev/null 2>&1; then
  exec python3 "$script_dir/vva_client.py" "$@"
fi

if command -v python >/dev/null 2>&1; then
  exec python "$script_dir/vva_client.py" "$@"
fi

echo "VVA 需要 Python 3，但当前电脑未找到 python3 或 python。" >&2
exit 127
