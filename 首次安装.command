#!/bin/zsh
set -eu
cd "${0:A:h}"
finish() { if [[ -t 0 ]]; then read -r "?按回车关闭…"; fi; }
trap 'print "安装尚未完成。请按上方提示处理后重新运行；已有文件会保留。"; finish' ZERR
print "PhonePilot · 准备本地运行环境"
if [[ "$(uname -s)" != Darwin ]]; then
  print "请在 Mac 上运行此安装文件。"
  finish
  exit 1
fi
if ! command -v uv >/dev/null 2>&1; then
  print "尚未检测到 uv（用于安装 Python 和项目依赖）。"
  print "安装说明：https://docs.astral.sh/uv/getting-started/installation/"
  finish
  exit 1
fi
# The project lock selects a compatible Python; uv may download it when missing.
uv sync --locked --python 3.14
print "依赖已安装，正在检查 Xcode 准备情况…"
if .venv/bin/python -m phonepilot.doctor; then
  print "安装检查通过。双击「启动 PhonePilot.command」开始连接。"
else
  print "运行环境已安装。完成上方提示的准备后，可重新运行本文件检查。"
fi
finish
