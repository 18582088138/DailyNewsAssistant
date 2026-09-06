# 14 语音合成指南 / Text-to-Speech Guide

> **TTS 跑在本地，一分钱都不花。**但它很花时间——实测 RTF ≈ 2.5，
> 也就是说生成 1 分钟音频要算 2.5 分钟。长文案的音频要跑半小时以上。
> 后面所有要出声的功能（P6 视频版、P7 播客版）都建立在这一层上。

---

## 一、两个后端，同一个接口

| provider | 跑在哪 | 模型 | 何时用 |
|---|---|---|---|
| `qwen3_ov` | Intel CPU / 核显 / NPU | 已转换的 OpenVINO IR | **本机默认**，已验证 |
| `qwen3_torch` | NVIDIA CUDA（也支持 cpu / mps） | 原始 HuggingFace 权重 | 有 N 卡的部署环境 |

换环境只改 `.env` 里的 `TTS_PROVIDER` 一行。业务代码、命令行、界面全都不动——
`produce/` 拿到的永远是 `TTSProvider` 协议，不知道背后是谁，和 `dna/llm/` 是同一套做法。

```
produce/  ──▶  tts/  ──▶  qwen3_ov     OpenVINO
                    ├──▶  qwen3_torch  PyTorch
                    └──▶  http service （TTS 部署成服务之后补，接口不变）
```

两个后端的调用签名完全一致，公共部分（分段、拼接、停顿、进度、逐段容错）
在 `tts/qwen3_base.py` 里只有一份，各自只实现「怎么加载模型」。

---

## 二、配置

```ini
# .env
TTS_PROVIDER=qwen3_ov

# --- qwen3_ov：OpenVINO 后端 ---
QWEN3_TTS_MODEL_DIR=C:/Users/test/Downloads/xkd/Models/Qwen3-TTS-CustomVoice-0.6B-OV
QWEN3_TTS_HELPER_DIR=C:/Users/test/Downloads/xkd/openvino_notebooks/notebooks/qwen3-tts

# --- qwen3_torch：PyTorch 后端 ---
QWEN3_TTS_TORCH_MODEL_DIR=

# --- 两个后端都要：Qwen3-TTS 源码仓库（提供 qwen_tts 包）---
QWEN3_TTS_REPO_DIR=C:/Users/test/Downloads/xkd/Models/Qwen3-TTS

TTS_DEVICE=GPU          # qwen3_ov：CPU|GPU|NPU　　qwen3_torch：cuda|cuda:0|cpu|mps
TTS_DTYPE=bfloat16      # 仅 qwen3_torch
TTS_VOICE_HOST=         # 留空 → serena
TTS_VOICE_GUEST=        # 留空 → uncle_fu
```

### `QWEN3_TTS_REPO_DIR` 为什么要单独配

`qwen_tts` 包是 **editable 安装**的，安装记录里存的是当时那个路径。
2026-09-04 实测：源码仓库从 `openvino_notebooks/` 搬到 `Models/` 之后，
`import qwen_tts` 直接 `ModuleNotFoundError`，而报错信息完全看不出是搬家导致的。

`dna doctor` 有一条专门的检查会把这件事说出来，`get_tts()` 也会把配好的路径
加进 `sys.path` 兜底。

### `TTS_DEVICE` 是真的会生效的

参考实现 `qwen_3_tts_helper.py` 里写着 `tmp_device = "GPU"`，
传进去的 `device` 参数**只对 speaker encoder 生效**——照原样用的话这个配置是死的。

本项目在加载时把两个类临时换成固定设备的子类，让配置真的传下去
（`tts/qwen3_openvino.py::_load_on_device`）。声码器保持在 CPU，与上游一致。

> ⚠️ 上游那句 `Loading OpenVINO Talker on GPU` 的打印读的是它自己的局部变量，
> **替换之后这句话就不再是真的了**。以 `dna tts` 显示的为准。

---

## 三、命令

```bash
dna tts                                     # 后端能不能起来、有哪些音色（秒回，不合成）
dna tts --say "一句话" -o out.wav           # 真的合成一次，看 RTF 与音质
dna produce <id> --kind narration_audio     # 口播音频
dna produce <id> --kind shortvideo_audio    # 短视频音频
dna produce <id> --kind longform_audio      # 长文案音频（几十分钟）
```

**换部署环境后第一件事就跑 `dna tts`。**它只加载模型、列音色，不合成，
几秒就能确认「模型在 + 设备对 + 包导得到」三件事。

---

## 四、界面里怎么用

工作台展开任意口播类产物（短视频 / 口播 / 长文案）→ 面板右侧有「合成音频」。

- **按钮不是琥珀色。**琥珀色在这个界面里专表示「这会计费」；音频不花钱，
  用同一个颜色会让「花钱」这个信号贬值。tooltip 里写的是预估等待时间。
- 稿子还没生成时按钮禁用——先有稿子才有音频。
- 预计超过 5 分钟的会先弹确认框（长文案音频总会弹）。
- 合成期间提示条显示 `12/47 段（已产出 83 秒音频）`——半小时的等待不能只给一个转圈。
- 合成完成后，同一面板里的耳机图标变成可点的下载按钮。

---

## 五、时长与代价

实测（本机 Intel 核显，`qwen3_ov`）：

| | 字数 | 音频 | 耗时 | RTF |
|---|---|---|---|---|
| 一句话 | 46 | 8.4s | 22.3s | 2.67 |
| 口播稿 | 827 | 153s | ~6.5min | ~2.5 |
| 长文案 | ~5500 | ~15min | **~37min** | ~2.5 |

因此三种音频**都不进 `dna produce --all`**。护栏的理由和长文案不同：
长文案拦的是账单，音频拦的是「点一下之后这台机器半小时没法用」。

### 实测语速与文案时长估算对不上

口播稿的估算是 93.9 秒，实际合成出来 153 秒——**差 63%**。
实测 Qwen3-TTS 的中文语速是 **5.4 字/秒**，而文案层按 4.5 字/秒（纯中文）
配 1.5 倍混排系数在算。

**本阶段不动文案层的换算。**发布时视频会加速播放，而加速倍率是人定的；
在没确定倍率之前调整语速常数，只会把一个偏差换成另一个偏差。
详见 [issues/009](issues/009-tts-speaking-rate.md)。

---

## 六、文本是怎么变成声音的

```
产物 .md ──▶ spoken_text() ──▶ clean_for_speech() ──▶ split_for_speech() ──▶ 逐段合成 ──▶ 拼接
            取出「口播」那段      去 URL / 标记        按句子边界切
```

三件事各自都有非做不可的理由：

1. **`spoken_text`**：产物抬头里有 `> 口播文案　·　来源：https://…`。
   整篇照念的话，模型会把网址一个字符一个字符读出来——不是音质变差，是整段废掉。
2. **`clean_for_speech`**：星号会被念成别的东西，长文案的提纲藏在 HTML 注释里。
3. **`split_for_speech`**：整段送进去又慢又容易在结尾掉字。
   **只在句子边界切**——每段是独立一次合成，句中切开拼起来能听见断裂与语调重置。

长文案走的是另一条路：它的 `.json` 附件已经按发言人切好 turns，
访谈的两个角色直接对应两把嗓子，不必回头解析 Markdown。

**一段失败不毁整篇。**长文案有几十段，跑到第 40 段崩掉就把前面的半小时赔进去。
缺段的音频照样落盘，但会明确报出缺了几段——残缺的音频被当成成品发出去才是最坏的结果。

---

## 七、在有 N 卡的机器上跑

本机是 `torch 2.8.0+cpu`（CPU-only 构建）且没有 NVIDIA 卡，
所以 `qwen3_torch` 后端**交付的是代码与离线单测，没有真机联调**——
和飞书机器人（P9）同一个处理方式：环境不具备就不假装验证过。

```bash
pip install torch --index-url https://download.pytorch.org/whl/cu124
huggingface-cli download Qwen/Qwen3-TTS-CustomVoice-0.6B --local-dir <权重目录>

# .env
TTS_PROVIDER=qwen3_torch
QWEN3_TTS_TORCH_MODEL_DIR=<权重目录>
QWEN3_TTS_REPO_DIR=<Qwen3-TTS 源码仓库>
TTS_DEVICE=cuda:0
TTS_DTYPE=bfloat16      # Turing 及更早的卡不支持 bf16，改 float16

dna tts --say "测试一句话"
```

设备与精度都在**加载权重之前**校验：CPU-only 的 torch 请求 cuda 时，
让 PyTorch 自己抛要先把权重全读进内存才失败，先查一遍几毫秒就能给出照做即可的提示。

---

## 八、相关

- 产物落盘规范：[05_output_spec.md](05_output_spec.md)
- 工作台使用：[13_workbench_guide.md](13_workbench_guide.md)
- 文案时长换算：`src/dna/narration/duration.py`
- 实现：`src/dna/tts/`（`base` · `segment` · `qwen3_base` · `qwen3_openvino` · `qwen3_torch` · `factory`）
