# 截图工具 (Screenshot Tool)

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
![Python](https://img.shields.io/badge/Python-3.11-blue.svg)
![PySide6](https://img.shields.io/badge/PySide6-6.11-green.svg)
![Platform](https://img.shields.io/badge/Platform-Windows%20%7C%20macOS-lightgrey.svg)
[![Release](https://img.shields.io/github/v/release/logilogi202604/screenshot_tool)](https://github.com/logilogi202604/screenshot_tool/releases/latest)

一个类似微信截图的桌面工具：全局热键唤起，框选区域，标注（矩形 / 椭圆 / 箭头 / 画笔 / 马赛克 / 序号 / 文字）、
多种颜色与线宽，放大镜取色，复制到剪贴板或保存为图片。基于 **Python + PySide6**，常驻系统托盘。

> **下载**：到 [Releases](https://github.com/logilogi202604/screenshot_tool/releases/latest) 下载
> 单文件 `ScreenshotTool.exe`（免装 Python），双击运行 → 托盘出现相机图标 → 按 `Alt+A` 截图。

## 功能

- **全局热键截图**：默认 `Alt+A`（微信式，可在配置里改），任意程序下按一下即可截图。
- **框选区域**：拖拽选择，带尺寸提示；选好后可拖动整体移动、拖 8 个手柄缩放。
- **放大镜**：选区前光标处显示像素放大镜 + 十字线 + 坐标 / RGB 取色。
- **标注工具**：矩形、椭圆、箭头、画笔（手绘）、**马赛克**、**序号**（自动递增编号）、文字。
- **颜色 / 线宽**：8 个预设颜色 + 自定义取色；细 / 中 / 粗三档线宽。
- **文字**：点击放置文本框，支持中文，`Enter` 提交、`Shift+Enter` 换行、`Esc` 取消；
  已添加的文字可**点住拖动改位置**，**双击重新编辑**（保留原颜色/字号）。
- **撤销 / 保存 / 复制**：`Ctrl+Z` 撤销，`Ctrl+S` 保存 PNG/JPG，`Ctrl+C` 或 `Enter` 复制到剪贴板。
- **截图发给 Claude Code**：确认时自动保存一份带时间戳的 PNG 并把路径放进剪贴板，详见下方「发给 Claude Code」。
- **高分屏**：自动处理 DPI 缩放，导出为屏幕原始分辨率，清晰不糊。

## 安装

**Windows**（仅需 PySide6）：

```powershell
pip install -r requirements.txt
```

**macOS**（PySide6 + pynput，建议用虚拟环境）：

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

## 运行

**Windows**：

```powershell
python main.py
```

或双击 `run.bat`（用 `pythonw` 启动，无控制台窗口）。

**macOS**：

```bash
python3 main.py
```

或双击 `run.command`（在 Finder 里双击即可，关闭终端窗口即退出）。

启动后程序进入**系统托盘 / 菜单栏**（蓝色相机图标）。图标菜单里有「截图 / 打开保存目录 / 退出」。
Windows 下单击或双击图标即可直接截图；macOS 下点菜单栏图标、从菜单选「截图」，或直接按热键。

> **开机自启（Windows）**：把 `run.bat` 的快捷方式放进 `shell:startup`（Win+R 输入即可打开启动文件夹）。
> **开机自启（macOS）**：系统设置 › 通用 › 登录项，把 `ScreenshotTool.app`（或 `run.command`）加进去。

## macOS 权限（重要）

macOS 会拦截「截屏」和「全局热键」，**首次运行需授予下列权限**，否则功能异常：

| 权限 | 路径 | 不授予的后果 |
|------|------|--------------|
| **屏幕录制** Screen Recording | 系统设置 › 隐私与安全性 › 屏幕录制 | 截到的是黑屏 / 只有壁纸 |
| **输入监控** Input Monitoring | 系统设置 › 隐私与安全性 › 输入监控 | 全局热键不触发（仍可点菜单栏图标截图） |
| **辅助功能** Accessibility | 系统设置 › 隐私与安全性 › 辅助功能 | 同上：某些 macOS 版本下 pynput 的键盘事件必须靠它才收得到 |

> **关于输入监控 vs 辅助功能**：热键用的是 pynput 的 Quartz 事件监听，不同 macOS 版本要求的开关不一致——
> pynput 官方要求「辅助功能」，Catalina 及以后又常要「输入监控」。**建议两个都开**；若只开一个后热键仍不触发，就把另一个也打开。

把你运行它的**宿主程序**加进上面各列表并打勾：
- 从源码运行 → 加 **终端 Terminal**（或 iTerm）；
- 用打包好的 `.app` → 加 **ScreenshotTool**。

改完权限**要重启本程序**（有时需退出并重新打开终端）才生效。

> macOS 默认热键是 **Cmd+Shift+A**（`⌘⇧A`）。之所以不用 Windows 那样的 Alt+A：pynput 无法“吃掉”
> 触发键，Option+A 会往当前输入框漏一个「å」；Cmd 组合键不产生文本，更干净。改热键见下方「配置」。

## 操作流程

1. 按 `Alt+A`（或点托盘菜单「截图」）→ 屏幕变暗。
2. 拖拽框选要截的区域 → 松开鼠标，底部出现工具栏。
3. 在工具栏选工具和颜色，在选区内绘制标注；马赛克拖框打码，序号点一下放一个递增编号，文字工具点一下即可输入。
4. `Enter` 或工具栏 ✓ → 复制到剪贴板并关闭；`Ctrl+S` 保存为文件；`Esc` 或 ✕ 取消。

### 工具栏

矩形 ▭ ｜ 椭圆 ○ ｜ 箭头 ↗ ｜ 画笔 ✎ ｜ 马赛克 ▦ ｜ 序号 ① ｜ 文字 T ｜ 线宽 ｜ 颜色 ｜ ↶撤销 💾保存 复制 ✕取消 ✓完成

### 快捷键

| 按键 | 作用 |
|------|------|
| `Alt+A` | 唤起截图（全局，可配置） |
| 拖拽 | 框选 / 调整选区 / 绘制标注 |
| 双击选区 | 复制并完成 |
| `Enter` | 复制到剪贴板并完成 |
| `Ctrl+C` | 复制到剪贴板 |
| `Ctrl+S` | 保存为图片 |
| `Ctrl+Z` | 撤销上一步标注 |
| `Esc` | 取消 |

## 发给 Claude Code

Windows 的终端**无法直接 `Ctrl+V` 粘贴剪贴板里的图片**（这是 Claude Code 在 Windows 上的已知限制，所有终端都一样）。
所以本工具在你**确认截图**（`Enter` / ✓ / 复制）时会：

1. 把图片放进剪贴板（可正常粘贴进微信、文档等）；
2. **自动保存一份带时间戳的 PNG** 到保存目录；
3. 把该 PNG 的**文件路径**也放进剪贴板。

于是给 Claude Code 发图有两种可靠方式：

- **拖拽**（最稳）：从资源管理器把刚保存的 PNG **拖进 Claude Code 终端窗口**，会自动插入路径，CC 即可读图。
- **粘贴路径**：在 CC 终端按 `Ctrl+V`，粘进去的是文件路径，CC 会识别并读取该图片。

> 托盘会弹通知告诉你保存到了哪里。若你只想要「纯图片进剪贴板」的老行为（粘进聊天软件），
> 把配置里的 `autosave_on_copy` 设为 `false` 即可。

## 配置

首次运行会在 `~/.screenshot_tool/config.json`（即
`C:\Users\<你>\.screenshot_tool\config.json`）生成默认配置：

```json
{
  "hotkey": { "ctrl": false, "alt": true, "shift": false, "win": false, "key": "A" },
  "save_dir": "C:\\Users\\<你>\\Pictures\\Screenshots",
  "default_color": "#ff3b30",
  "default_width": 4,
  "default_font_size": 18,
  "autosave_on_copy": true
}
```

> **改热键**：把 `hotkey` 里的 `ctrl`/`alt`/`shift`/`win` 设为 `true`/`false`，`key` 改成想要的键
> （如 `"F1"`、`"Q"`）。例如想用 `Ctrl+Shift+A` 就是
> `{"ctrl": true, "alt": false, "shift": true, "win": false, "key": "A"}`。**改完需重启程序生效。**

## 打包成单文件 exe

已配好 PyInstaller。生成图标后构建：

```powershell
pip install pyinstaller
python gen_icon.py      # 生成 app.ico
build.bat               # 或手动执行下面的命令
```

手动命令：

```powershell
python -m PyInstaller --noconfirm --clean --onefile --windowed `
  --name ScreenshotTool --icon app.ico main.py
```

产物为 `dist\ScreenshotTool.exe`，**单文件、无需安装 Python**，双击即可常驻托盘。
> onefile 首次启动会解压到临时目录，比源码运行稍慢 1～2 秒，属正常现象。

### macOS：打包成 .app

```bash
pip install pyinstaller
./build_mac.sh
```

产物为 `dist/ScreenshotTool.app`。脚本会自动把 `LSUIElement` 设为 `true`（纯菜单栏 App，不占
程序坞图标），并用 `--collect-submodules pynput` 确保热键后端被打进包里。首次运行同样需要在
「隐私与安全性」里给 `ScreenshotTool` 授予**屏幕录制**和**输入监控**权限（见上文）。
> 未签名的 `.app` 首次打开会被 Gatekeeper 拦，右键点图标选「打开」放行一次即可。

## 项目结构

| 文件 | 说明 |
|------|------|
| `main.py` | 入口：系统托盘 + 全局热键注册 + 唤起遮罩（跨平台） |
| `hotkey.py` | 按平台选择热键后端的调度层 |
| `hotkey_win.py` | Windows 后端：Win32 `RegisterHotKey`（ctypes）+ Qt 原生事件过滤器 |
| `hotkey_mac.py` | macOS 后端：`pynput` 监听线程 + 跨线程回主线程发信号 |
| `hotkey_null.py` | 其它平台的空后端（热键不可用，仍可点托盘图标） |
| `hotkey_common.py` | 各后端共用的 `describe_hotkey`（组合键文案，平台相关标签） |
| `single_instance.py` | 单实例锁：Windows 用命名互斥量，POSIX 用 `flock` |
| `overlay.py` | 全屏遮罩：选区、缩放手柄、放大镜、标注、内联文字、导出（纯 Qt，跨平台） |
| `toolbar.py` | 浮动工具栏（工具 / 颜色 / 线宽 / 撤销 / 保存 / 复制 / 完成） |
| `annotations.py` | 标注图形类（矩形 / 椭圆 / 箭头 / 画笔 / 文字） |
| `config.py` | 配置读写（默认热键按平台区分） |
| `run.bat` / `build.bat` | Windows 启动 / 打包（PyInstaller `.exe`） |
| `run.command` / `build_mac.sh` | macOS 启动 / 打包（PyInstaller `.app`） |
| `test_*.py` | 冒烟测试：核心渲染 / 全局热键 / 真实截图 |

## 测试

```powershell
# 核心渲染逻辑（离屏，无需显示器）
$env:QT_QPA_PLATFORM = "offscreen"; python test_smoke.py
# 全局热键端到端（会注册 Ctrl+Alt+Shift+F12 并模拟按下）
python test_hotkey.py
# 真实截屏导出（生成一张带标注的 PNG）
python test_capture.py
```

## 已知限制

- **Windows / macOS 双平台**。热键实现按平台分离：Windows 用 Win32 `RegisterHotKey`（系统级、会
  “吃掉”触发键）；macOS 用 `pynput`（需「输入监控」权限，且**不会吃掉**触发键，故默认用不产生文本
  的 Cmd 组合）。Linux 暂无热键后端，但仍可点托盘图标截图。
- **macOS 需要两项系统权限**（屏幕录制 / 输入监控），见上文；未授予时对应功能失效。
- 多显示器**混合不同 DPI** 时截图可能有缩放误差；相同缩放比例下正常。

## 许可证

[MIT](LICENSE) © logilogi202604
