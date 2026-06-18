@echo off
REM Build a single-file Windows executable with PyInstaller.
REM Output: dist\ScreenshotTool.exe
cd /d "%~dp0"

python -m PyInstaller ^
  --noconfirm --clean ^
  --onefile ^
  --windowed ^
  --name ScreenshotTool ^
  --icon app.ico ^
  main.py

echo.
echo Done. Executable is in: %~dp0dist\ScreenshotTool.exe
pause
