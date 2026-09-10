# 09 · 启动与打包 / Launching and packaging

两条路，**难度差一个数量级**：

| 方式 | 状态 | 适用 |
|---|---|---|
| `start.bat` + 桌面快捷方式 | ✅ 当场可用 | 装了 conda 的机器（就是本机） |
| PyInstaller 打包成 exe | 🟡 配置就绪，**未实际构建** | 给没有 Python 环境的机器 |

第二条与用户确认过：**这一轮只把打包需要的改动做完，exe 的实际构建与调试是独立一步。**
理由是打包的坑几乎全在「构建出来之后跑起来才发现」那一侧，而配置本身可以先做对。

---

## 1 · 一键启动（现在就能用）

```
start.bat            双击即启动，浏览器自动打开
create_shortcut.ps1  跑一次，桌面上多一个 DailyNews Workbench
```

`.bat` 只做两件事：切到仓库根，然后用 `ov_env_py312` 的解释器跑
`python -m frontends.cli.main gui`。

三个已经踩过的点：

- **直接指到 `python.exe`，不用 `conda activate`。** 后者在 `cmd.exe` 里要先
  `conda init` 过，没 init 的机器上报的是「'conda' 不是内部或外部命令」——
  看起来像 conda 没装。`.bat` 里仍留了 `conda run` 作为兜底（换过安装位置时）。
- **用 `-m frontends.cli.main` 而不是 `dna`。** 没 `pip install -e .` 过的机器上
  `dna` 命令不存在，而 `-m` 只要解释器和源码在就能跑。
- **快捷方式必须设工作目录。** `config/`、`data/`、`outputs/` 都是按仓库根解析的
  相对路径；不设的话双击后程序在 `C:\Windows\System32` 里找配置，
  然后**用一个空库正常跑起来**——不报错，只是什么都没有。

---

## 2 · 打包的关键前置：仓库根怎么定位

这是打包必踩、且最难查的一个坑。`core/config.py`：

```python
if os.environ.get("DNA_HOME"):
    PROJECT_ROOT = Path(os.environ["DNA_HOME"]).resolve()
elif getattr(sys, "frozen", False):
    PROJECT_ROOT = Path(sys.executable).resolve().parent
else:
    PROJECT_ROOT = Path(__file__).resolve().parents[3]
```

PyInstaller 下 `__file__` 在**临时解包目录**里（onefile 是 `%TEMP%\_MEIxxxxx`）。
按源码那套上溯三层，`config/`、`.env`、`data/`、`outputs/` 会全部指到那儿——
每次启动都是一个空台账、一个空产物目录，而且**一行错误都不报**。

`DNA_HOME` 不是为打包加的，是为**测试**加的：没有它，验证 frozen 分支要真去打一个包。
（`tests/core/test_config.py` 里三种定位各一条。）

---

## 3 · 打包配置

```
packaging/
├── entry_gui.py      入口脚本（PyInstaller 需要一个脚本，Typer 应用不能当入口）
├── dna_gui.spec      构建配置
└── build.ps1         构建 + 把 config/ 拷到 exe 旁边
```

```powershell
powershell -ExecutionPolicy Bypass -File packaging\build.ps1
```

### 三个决定

**onedir，不是 onefile。** onefile 每次启动都要把几百 MB 解包到临时目录，冷启动十几秒,
而这是天天双击的工具。

**`config/`、`.env`、`data/`、`outputs/` 不打进包，放在 exe 旁边。**
提示词外置的整个前提就是人能改它（见 `docs/06_prompt_spec.md`）——打进包就改不动了。
数据和产物更不能进包：那是用户的东西。

**保留控制台窗口。** 抓取进度、LLM 报错、失败原因都在标准输出里；
藏掉之后「双击没反应」是用户唯一能提供的现象。
`entry_gui.py` 另外把顶层异常写到 exe 旁边的 `startup_error.log`，双保险。

### 依赖与体积

| 项 | 说明 |
|---|---|
| PyInstaller | `pip install pyinstaller`，不在 `pyproject` 的依赖里 |
| 预期体积 | **300~600 MB**（nicegui 的静态资源 + trafilatura/lxml + yt-dlp 的全部提取器） |
| 排除 | `openvino` / `torch` / `transformers` / `optimum`——本地 LLM 方向已冻结，打进来是好几个 GB |

---

## 4 · 已知坑（构建之后才会发现的那些）

1. **NiceGUI 的静态资源必须显式收集**（spec 里的 `collect_data_files("nicegui")`）。
   漏了不报导入错误：服务起得来，浏览器打开是**一片空白**——最容易误判成端口问题。
2. **yt-dlp 的提取器是按平台一个模块动态加载的**，所以用 `collect_submodules`。
   收不全的表现是「这个网站的视频抓不了」而其他网站正常，比整体失败难查得多。
   它跟着平台改版走，需要定期 `pip install -U yt-dlp`——**打包后的那份不会自动更新**,
   这是 exe 的一个固有代价。
3. **Playwright 的浏览器内核不打包。** 它是几百 MB 的独立下载，装在
   `%LOCALAPPDATA%\ms-playwright`。没有它只影响需要浏览器渲染的少数站点（会降级为
   标题+链接），不影响主流程。要用就在目标机器上单独 `playwright install chromium`。
4. **`multiprocessing.freeze_support()` 不能漏**（在 `entry_gui.py` 第一行）。
   Windows 上子进程是**重新启动 exe** 来创建的，不调这句会无限套娃开界面。
5. **杀软误判。** 未签名的 PyInstaller exe 常被拦。`upx=False` 能减少一部分误判
   （UPX 压过的更容易中招），但根治要代码签名证书。
6. **`.env` 里是真实密钥。** `build.ps1` 会把本机的 `.env` 拷到 `dist/` 旁边——
   **那份 dist 不能发给别人**。要分发就删掉它，换成 `.env.example`。

---

## 5 · 相关文件

| 文件 | 作用 |
|---|---|
| `start.bat` | 双击启动（当前唯一实际验证过的路径） |
| `create_shortcut.ps1` | 桌面快捷方式，跑一次 |
| `packaging/entry_gui.py` | 冻结后的入口 |
| `packaging/dna_gui.spec` | 构建配置 |
| `packaging/build.ps1` | 构建 + 拷配置 |
| `src/dna/core/config.py` | `PROJECT_ROOT` 的三种定位 |
