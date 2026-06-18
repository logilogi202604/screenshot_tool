# 截图工具 (Screenshot Tool)

一个类似微信截图的桌面工具：全局热键唤起，框选区域，标注（矩形 / 椭圆 / 箭头 / 画笔 / 马赛克 / 序号 / 文字）、
多种颜色与线宽，放大镜取色，复制到剪贴板或保存为图片。基于 **Python + PySide6**，常驻系统托盘。

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

```powershell
pip install -r requirements.txt   # 仅需 PySide6
```

## 运行

```powershell
python main.py
```

或双击 `run.bat`（用 `pythonw` 启动，无控制台窗口）。

启动后程序进入**系统托盘**（蓝色相机图标）。右键托盘图标可「截图 / 打开保存目录 / 退出」，
双击托盘图标也能直接截图。

> 开机自启：把 `run.bat` 的快捷方式放进 `shell:startup`（Win+R 输入即可打开启动文件夹）。

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

## 项目结构

| 文件 | 说明 |
|------|------|
| `main.py` | 入口：系统托盘 + 全局热键注册 + 唤起遮罩 |
| `hotkey.py` | Win32 `RegisterHotKey`（ctypes）+ Qt 原生事件过滤器 |
| `overlay.py` | 全屏遮罩：选区、缩放手柄、放大镜、标注、内联文字、导出 |
| `toolbar.py` | 浮动工具栏（工具 / 颜色 / 线宽 / 撤销 / 保存 / 复制 / 完成） |
| `annotations.py` | 标注图形类（矩形 / 椭圆 / 箭头 / 画笔 / 文字） |
| `config.py` | 配置读写 |
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

- 仅支持 **Windows**（全局热键用的是 Win32 API）。
- 多显示器**混合不同 DPI** 时截图可能有缩放误差；相同缩放比例下正常。
