# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller 打包配置 / PyInstaller build spec.

    pyinstaller packaging/dna_gui.spec --noconfirm      # 从仓库根跑

**这一轮只把配置做对，exe 的实际构建与调试是独立一步**（与用户确认过）。
This spec is complete; actually building and debugging the exe is a separate step.

三个必须记住的决定 / Three decisions worth remembering:

1. **onedir，不是 onefile。** onefile 每次启动都要把几百 MB 解包到临时目录,
   冷启动十几秒；而这是个天天双击的工具。onedir 还让 `config/prompts/` 能被人直接改。
   onefile would unpack hundreds of MB on every launch; this is a daily-use tool.

2. **`config/`、`.env`、`data/`、`outputs/` 不打进包，放在 exe 旁边。**
   提示词外置的**整个前提**就是人能改它——打进包就改不动了，
   而且 onefile 下改了也白改（下次启动重新解包覆盖）。
   数据和产物更不能进包：那是用户的东西，不是程序的一部分。
   Kept beside the exe: editable prompts are the entire point of externalising them, and
   data and outputs belong to the user, not the program.

3. **`sys.frozen` 分支已经在 `core/config.py` 里了。** 冻结后 `__file__` 在临时解包
   目录里，按源码那套上溯三层会让 config/.env/data/outputs 全指到临时目录——
   每次启动都是一个空库，而且**不报错**。这是打包必踩且最难查的一个坑。
   Without that branch every path resolves into the unpack directory: a fresh empty
   database on every launch, with no error.
"""

from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

# spec 文件里没有 `__file__`；SPECPATH 由 PyInstaller 注入
ROOT = Path(SPECPATH).parent  # noqa: F821

# NiceGUI 的静态资源（Vue/Quasar/Tailwind 的 js 与 css）**必须显式收集**。
# 漏了不会报导入错误：服务起得来，浏览器打开是一片空白——最容易误判成端口问题。
# Required: without them the server starts and the browser shows a blank page.
datas = collect_data_files("nicegui")

hiddenimports = [
    # 正文抽取与订阅解析：都是运行时才用到的重库，静态分析看不到全部子模块
    "trafilatura",
    "feedparser",
    # yt-dlp 的提取器是按平台一个模块、按名字动态加载的，收不全就变成
    # 「这个网站的视频抓不了」而其他网站正常——比整体失败更难查
    # yt-dlp loads per-site extractors dynamically; a partial collection looks like
    # "videos from this one site fail" rather than an outright error.
    *collect_submodules("yt_dlp"),
    # dna 自己的子模块有按需导入的（tts / llm 的 provider 工厂）
    *collect_submodules("dna"),
]

excludes = [
    # 本地 LLM 与 OpenVINO 方向已冻结，打进来只是几个 GB 的体积
    # The local LLM / OpenVINO direction is frozen; bundling it only adds gigabytes.
    "openvino",
    "torch",
    "transformers",
    "optimum",
    # 测试与开发工具
    "pytest",
    "IPython",
    "matplotlib",
]

a = Analysis(  # noqa: F821
    [str(ROOT / "packaging" / "entry_gui.py")],
    pathex=[str(ROOT), str(ROOT / "src")],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)

pyz = PYZ(a.pure)  # noqa: F821

exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="DailyNews",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,  # UPX 压过的 exe 常被杀软误判，省下的体积换不来这个麻烦
    # **保留控制台。** 这是个开发工具：抓取进度、LLM 报错、失败原因都在标准输出里,
    # 藏掉之后「双击没反应」是用户唯一能提供的现象。
    # Console kept: this is a dev tool, and hiding it reduces every failure to
    # "nothing happens".
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(  # noqa: F821
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="DailyNews",
)
