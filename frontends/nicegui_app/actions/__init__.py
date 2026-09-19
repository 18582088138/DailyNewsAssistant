"""
界面动作 / Workbench actions.

界面按钮与核心逻辑之间的唯一桥梁。**这里不写业务逻辑**——每个函数都只是
「调 dna.produce / dna.store，把结果整理成界面好显示的形状」。
The single bridge between the buttons and the core. No business logic lives here.

为什么必须走 io_bound / Why everything goes through io_bound:
    一次 LLM 调用要十几秒，长文案要一两分钟。直接在事件回调里同步调用会**冻住
    整个界面**——连滚动和点别的按钮都不行，看起来就像程序崩了。
    A single call takes ten-odd seconds; calling synchronously inside an event handler
    freezes the whole page and looks exactly like a crash.

这个 `__init__` 是**显式 facade，不是兼容壳**：界面各处都写
`actions.load_rows(...)`、`actions.run_production(...)`，属性式调用就是这个模块
对外的边界。拆成八个文件是为了改的时候不必在一千四百行里找，而不是为了
让调用方改成八个 import。
A deliberate facade: the UI calls these as attributes of one module, and that is the
module's boundary. The split exists to make edits cheap, not to move the seam.

分在哪几个文件里 / Where things live:
    rows         一行长什么样、一页怎么取
    run          跑生成（后台线程）
    intake       粘链接 / 从订阅抓
    tts_actions  TTS 操作台的后端动作
    paths        产物路径与达标判定
    batch        批量重抓 / 批量删除
    reveal       在系统文件管理器里打开目录（纯 OS 集成）
    env          `.env` 白名单与设置面板读写
"""

from frontends.nicegui_app.actions.batch import (
    batch_delete,
    batch_refetch,
    plan_batch_delete,
)
from frontends.nicegui_app.actions.env import (
    ENV_FIELDS,
    EnvField,
    cache_status,
    env_display,
    env_groups,
    env_shadowed,
    profile_values,
    ref_audio_file,
    ref_audio_options,
    save_settings,
    tts_voice_options,
)
from frontends.nicegui_app.actions.intake import (
    import_from_sources,
    import_links,
    source_options,
)
from frontends.nicegui_app.actions.paths import (
    article_directory,
    body_file,
    media_folders,
    media_target,
    over_target,
    production_file,
    production_sidecar,
    production_text,
    subtitle_file,
    target_window,
)
from frontends.nicegui_app.actions.reveal import (
    open_in_file_manager,
)
from frontends.nicegui_app.actions.rows import (
    SCAN_CAP,
    PageView,
    RowView,
    load_rows,
)
from frontends.nicegui_app.actions.run import (
    audio_estimate_seconds,
    audio_for,
    last_instructions,
    longform_estimate,
    preview_links,
    run_production,
)
from frontends.nicegui_app.actions.tts_actions import (
    TTSStatus,
    preview_voice,
    resplit_text,
    save_script,
    script_is_editable,
    speech_segments,
    tts_start,
    tts_status,
    tts_voices,
    upload_ref_audio,
)

__all__ = [
    "ENV_FIELDS",
    "SCAN_CAP",
    "EnvField",
    "PageView",
    "RowView",
    "TTSStatus",
    "article_directory",
    "audio_estimate_seconds",
    "audio_for",
    "batch_delete",
    "batch_refetch",
    "body_file",
    "cache_status",
    "env_display",
    "env_groups",
    "env_shadowed",
    "import_from_sources",
    "import_links",
    "last_instructions",
    "load_rows",
    "longform_estimate",
    "media_folders",
    "media_target",
    "open_in_file_manager",
    "over_target",
    "plan_batch_delete",
    "preview_links",
    "preview_voice",
    "production_file",
    "production_sidecar",
    "production_text",
    "profile_values",
    "ref_audio_file",
    "ref_audio_options",
    "resplit_text",
    "run_production",
    "save_script",
    "save_settings",
    "script_is_editable",
    "source_options",
    "speech_segments",
    "subtitle_file",
    "target_window",
    "tts_start",
    "tts_status",
    "tts_voice_options",
    "tts_voices",
    "upload_ref_audio",
]
