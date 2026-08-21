#!/bin/sh
# Pipe-safe: curl -LsSf https://raw.githubusercontent.com/Flowerf19/thyca-ai/main/install.sh | sh
set -eu

case "$(uname -s)" in
  Linux) ;;
  *)
    echo "thyca install: Linux only" >&2
    exit 1
    ;;
esac

THYCA_GIT="${THYCA_GIT:-git+https://github.com/Flowerf19/thyca-ai.git}"

if ! command -v uv >/dev/null 2>&1; then
  if ! command -v curl >/dev/null 2>&1; then
    echo "thyca install: curl is required to bootstrap uv" >&2
    exit 1
  fi
  curl -LsSf https://astral.sh/uv/install.sh | sh
  PATH="${HOME}/.local/bin:${XDG_BIN_HOME:-${HOME}/.local/bin}:${PATH:-}"
  export PATH
  if ! command -v uv >/dev/null 2>&1; then
    echo "thyca install: uv not on PATH after bootstrap" >&2
    exit 1
  fi
fi

uv python install 3.14
uv tool install --python 3.14 --force "$THYCA_GIT"

bin_dir="$(uv tool dir --bin)"
thyca_bin="${bin_dir}/thyca"
if [ ! -x "$thyca_bin" ]; then
  echo "thyca install: missing executable ${thyca_bin}" >&2
  exit 1
fi

on_path=0
case ":${PATH:-}:" in
  *":${bin_dir}:"*) on_path=1 ;;
esac
if [ "$on_path" -eq 0 ]; then
  uv tool update-shell || true
  printf 'Add to this shell:\n  export PATH="%s:$PATH"\n' "$bin_dir"
fi

PATH="${bin_dir}:${PATH:-}"
export PATH
thyca --version
