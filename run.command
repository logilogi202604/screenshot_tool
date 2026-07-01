#!/bin/bash
# macOS launcher — double-click in Finder to start the screenshot tool.
# It's a menu-bar (tray) app; closing this Terminal window quits it.
cd "$(dirname "$0")"

PY=python3
[ -x ".venv/bin/python3" ] && PY=".venv/bin/python3"

echo "启动截图工具…（菜单栏出现相机图标后，按热键截图；关闭此窗口即退出）"
exec "$PY" main.py
