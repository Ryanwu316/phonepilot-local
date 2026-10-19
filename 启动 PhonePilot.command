#!/bin/zsh
set -eu
cd "${0:A:h}"
finish() { if [[ -t 0 ]]; then read -r "?按回车关闭…"; fi; }
if [[ ! -x .venv/bin/python ]]; then
  print "请先双击运行：首次安装.command"
  finish
  exit 1
fi
print "正在启动或复用 PhonePilot；不会自动配对或操作手机。"
if ! .venv/bin/python skills/phonepilot/scripts/session.py start --open; then
  print "未能启动。请重新运行首次安装，或检查原有服务和端口。"
  finish
  exit 1
fi
print "控制台已打开；关闭这个窗口不会停止手机控制台。"
finish
