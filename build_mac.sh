#!/bin/bash
# Build a macOS .app bundle with PyInstaller.
# Output: dist/ScreenshotTool.app
set -e
cd "$(dirname "$0")"

python3 -m PyInstaller \
  --noconfirm --clean \
  --windowed \
  --name ScreenshotTool \
  --osx-bundle-identifier com.logilogi.screenshottool \
  --collect-submodules pynput \
  main.py

PLIST="dist/ScreenshotTool.app/Contents/Info.plist"
if [ -f "$PLIST" ]; then
  # Run as a menu-bar agent: no Dock icon, no app-switcher entry.
  /usr/libexec/PlistBuddy -c "Add :LSUIElement bool true" "$PLIST" 2>/dev/null \
    || /usr/libexec/PlistBuddy -c "Set :LSUIElement true" "$PLIST"
fi

echo
echo "Done → dist/ScreenshotTool.app"
echo "首次运行需在「系统设置 › 隐私与安全性」授予权限："
echo "  • 屏幕录制（Screen Recording） — 否则截到的是黑屏/桌面壁纸"
echo "  • 输入监控（Input Monitoring）+ 辅助功能（Accessibility） — 否则全局热键不触发"
echo "    （不同 macOS 版本要求的开关不一，两个都开最稳）"
