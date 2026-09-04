"""
工作台主题 / Workbench theme.

**深色控制台**：这是一张每天要盯着看的数据表，不是落地页。所以走仪表盘/终端那一路
——深底、细线网格、等宽数字、一个高对比强调色，而不是白底加紫蓝渐变那套。
A dark console. This is a data table someone stares at daily, not a landing page, so it
borrows from dashboards and terminals: dark ground, hairline grid, tabular figures and a
single high-contrast accent — rather than the white-with-purple-gradient default.

颜色是有语义的 / The palette carries meaning:
    青绿 = 已生成，复用不花钱          琥珀 = 点下去会计费
    红   = 失败                        灰   = 本篇不适用
    **绝不能只靠颜色区分**：每种状态同时带一个形状符号（●／○／▲／—），
    色觉障碍者与黑白截图下都要能分辨。
    Never colour alone: every state also carries a glyph, so it survives colour-blindness
    and greyscale screenshots.

字体不走 CDN / Fonts are never fetched:
    公司代理会把 Google Fonts 拦下来，页面要么裸奔要么卡住。这里只用 Windows 上
    一定存在的字体栈，离线可用。
    The corporate proxy blocks Google Fonts, leaving the page either unstyled or hanging.
    Only locally present families are named, so it renders offline.
"""

from __future__ import annotations

from nicegui import ui

# 表格列宽（px）——表头与每一行共用同一套，才能对齐
# Column widths shared by the header and every row so the two stay aligned.
COL_TITLE_MIN = 260
COL_BODY = 78
COL_MEDIA = 88
COL_KIND = 108

# 标题最多显示多少个字符 / how many characters of a title are shown
# 超长标题会把整行挤变形。CSS 的 truncate 已经能兜住，但**显式截断更可控**：
# 中日文字符宽度是拉丁字母的两倍，纯靠像素宽度截，中文标题会比英文标题少露一半内容。
# CSS truncation alone would clip by pixel width, and since CJK glyphs are twice as wide
# as Latin ones a Chinese headline would show half as much as an English one.
TITLE_MAX_CHARS = 42

_CSS = """
:root {
  --wb-bg:        #0a0e14;
  --wb-panel:     #111823;
  --wb-panel-2:   #161f2c;
  --wb-line:      rgba(125, 165, 205, 0.14);
  --wb-line-strong: rgba(125, 165, 205, 0.30);
  --wb-text:      #c8d6e5;
  --wb-dim:       #7b8ea4;
  --wb-faint:     #55677a;
  --wb-accent:    #3ddc97;   /* 已生成：复用不花钱 */
  --wb-warn:      #f2a33c;   /* 会计费 */
  --wb-danger:    #ff6b6b;   /* 失败 */
  --wb-new:       #22d3ee;   /* 刚导入、还没动过 */
  --wb-mono: ui-monospace, "Cascadia Mono", "JetBrains Mono", Consolas, monospace;
  --wb-sans: "Microsoft YaHei", "Noto Sans SC", "PingFang SC", system-ui, sans-serif;
}

body, .nicegui-content {
  background: var(--wb-bg);
  color: var(--wb-text);
  font-family: var(--wb-sans);
}

/* 极淡的网格底纹：给深色背景一点纵深，又不至于抢内容
   A barely-there grid gives the dark ground some depth without competing with content. */
body::before {
  content: ""; position: fixed; inset: 0; pointer-events: none; z-index: 0;
  background-image:
    linear-gradient(rgba(125,165,205,.035) 1px, transparent 1px),
    linear-gradient(90deg, rgba(125,165,205,.035) 1px, transparent 1px);
  background-size: 44px 44px;
}
.nicegui-content { position: relative; z-index: 1; }

/* ---- 顶栏 / header ---- */
.wb-header {
  background: linear-gradient(180deg, #0f1622 0%, #0b111a 100%);
  border-bottom: 1px solid var(--wb-line-strong);
}
.wb-title { font-family: var(--wb-mono); letter-spacing: .06em; color: var(--wb-text); }
.wb-title .accent { color: var(--wb-accent); }

/* ---- 表格骨架 / table skeleton ----
   横向不够宽时**出现滚动条，而不是让列互相重叠**。
   先前用 flex + 固定宽度 + no-wrap，窗口一窄，flex-1 的标题被压到负宽度，
   后面的列就叠上来了。Grid + min-width 从根上没有这个可能。
   A narrow viewport now scrolls instead of overlapping: the previous flex layout let the
   flex-1 title collapse past zero width and the fixed columns rode over it. */
.wb-scroll {
  overflow: auto;
  max-height: calc(100vh - 208px);
  border: 1px solid var(--wb-line);
  border-radius: 10px;
  background: var(--wb-panel);
}
.wb-table { min-width: MIN_TABLE_WIDTHpx; }

.wb-grid {
  display: grid;
  grid-template-columns:
    minmax(COL_TITLE_MINpx, 1fr) COL_BODYpx COL_MEDIApx repeat(KIND_COUNT, COL_KINDpx);
  align-items: stretch;
}

/* ---- 表头冻结 / frozen header ---- */
.wb-head {
  position: sticky; top: 0; z-index: 20;
  background: var(--wb-panel-2);
  border-bottom: 1px solid var(--wb-line-strong);
  font-family: var(--wb-mono);
  font-size: 11px; letter-spacing: .08em; text-transform: uppercase;
  color: var(--wb-dim);
}
.wb-head > div { padding: 9px 8px; display: flex; align-items: center; }
.wb-head > div + div { justify-content: center; }

/* 列分隔线：文章多的时候，没有竖线根本对不上哪一列是哪个功能
   Column rules: with many rows there is no other way to tell which column is which. */
.wb-grid > div + div { border-left: 1px solid var(--wb-line); }
/* 抓取信息与产物之间加一道重线，把两组分开
   A heavier rule separates the fetch columns from the production columns. */
.wb-grid > div:nth-child(4) { border-left: 1px solid var(--wb-line-strong); }

/* ---- 行 / rows ---- */
.wb-rowwrap { border-bottom: 1px solid var(--wb-line); }
.wb-rowwrap:last-child { border-bottom: none; }
.wb-row { transition: background .12s ease; }
.wb-row:hover { background: rgba(125,165,205,.045); }
.wb-rowwrap.is-open .wb-row { background: rgba(61,220,151,.06); }
.wb-rowwrap.is-open { border-left: 2px solid var(--wb-accent); }

/* ---- NEW 标识 / the "new import" badge ----
   第四种颜色，和已生成（青绿）、计费（琥珀）、失败（红）都分得开。
   实心填充 + 深色字：在这个深底上比描边亮得多，扫一眼就能定位。
   A fourth colour, distinct from done, cost and failure. Solid fill with dark text reads
   far louder than an outline on this ground. */
.wb-new-badge {
  display: inline-flex; align-items: center;
  background: var(--wb-new); color: #04252b;
  font-family: var(--wb-mono); font-size: 9.5px; font-weight: 700;
  letter-spacing: .1em; line-height: 1;
  padding: 2px 5px; border-radius: 3px;
  box-shadow: 0 0 0 0 rgba(34, 211, 238, .55);
  animation: wb-pulse 2.4s ease-out infinite;
}
@keyframes wb-pulse {
  0%   { box-shadow: 0 0 0 0   rgba(34, 211, 238, .55); }
  70%  { box-shadow: 0 0 0 6px rgba(34, 211, 238, 0);   }
  100% { box-shadow: 0 0 0 0   rgba(34, 211, 238, 0);   }
}
/* 整行也标出来：只靠标题旁边一个小标签，横向滚动到右边就看不见了
   The row is marked too: a badge beside the title disappears once scrolled right. */
.wb-rowwrap.is-new { border-left: 2px solid var(--wb-new); }
.wb-rowwrap.is-new .wb-row { background: rgba(34, 211, 238, .045); }
/* 展开态压过新导入态——正在看的那一行比「还没看」更重要
   The open state wins: the row being read matters more than the fact it is unread. */
.wb-rowwrap.is-open.is-new { border-left-color: var(--wb-accent); }

.wb-cell-title { padding: 8px 10px; min-width: 0; cursor: pointer; }
.wb-cell-title .t {
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  font-size: 13px; color: var(--wb-text);
  /* flex 子项默认不肯缩到内容宽度以下，不给 min-width:0 的话
     ellipsis 永远不生效、标题会把 NEW 标识挤出去
     A flex item will not shrink below its content without this, so the ellipsis would
     never engage and a long title would push the badge out of the cell. */
  min-width: 0;
}
.wb-cell-meta { font-size: 10.5px; color: var(--wb-faint); font-family: var(--wb-mono); }

.wb-num {
  display: flex; align-items: center; justify-content: center;
  font-family: var(--wb-mono); font-size: 12px; color: var(--wb-dim);
  font-variant-numeric: tabular-nums;
}

/* ---- 产物格 / production cells ----
   整格可点，点开的是内容；**重做按钮不在这里**——它在展开面板里。
   先前把重做按钮直接放在格子上，一次误触就是一次计费。
   The whole cell opens the content. The redo button lives inside the panel, not here:
   on the cell surface a single mis-click cost money. */
.wb-kind {
  display: flex; flex-direction: column; align-items: center; justify-content: center;
  gap: 2px; padding: 6px 4px; cursor: pointer;
  font-family: var(--wb-mono); font-size: 11.5px;
  transition: background .12s ease;
}
.wb-kind:hover { background: rgba(125,165,205,.07); }
.wb-kind.sel { background: rgba(61,220,151,.10); box-shadow: inset 0 -2px 0 var(--wb-accent); }
.wb-kind .glyph { font-size: 13px; line-height: 1; }
.wb-kind .val { font-variant-numeric: tabular-nums; }
.wb-ok    { color: var(--wb-accent); }
.wb-empty { color: var(--wb-faint); }
.wb-fail  { color: var(--wb-danger); }
.wb-na    { color: var(--wb-faint); opacity: .5; cursor: not-allowed; }
.wb-over  { color: var(--wb-warn); }

/* ---- 展开面板 / detail panel ---- */
.wb-detail {
  background: #0d141d;
  border-top: 1px solid var(--wb-line-strong);
  padding: 12px 14px 14px;
}
.wb-detail .wb-body {
  max-height: 380px; overflow: auto;
  background: var(--wb-bg); border: 1px solid var(--wb-line);
  border-radius: 8px; padding: 10px 14px; font-size: 13px; line-height: 1.75;
}
.wb-detail .wb-body h1, .wb-detail .wb-body h2 { font-size: 15px; color: var(--wb-accent); }
.wb-detail .wb-body strong { color: #e6eef8; }
.wb-path {
  font-family: var(--wb-mono); font-size: 10.5px; color: var(--wb-faint);
  word-break: break-all;
}

/* 会花钱的按钮统一是琥珀色，视觉上和其它操作分开
   Anything that spends money is amber, visually separated from everything else. */
.wb-btn-cost { color: var(--wb-warn) !important; }

/* ---- 筛选栏 / filter bar ---- */
.wb-filters .q-field__control { background: var(--wb-panel); }
.wb-filters .q-field__native, .wb-filters .q-field__label { color: var(--wb-text); }

/* 减少动效偏好 / respect reduced motion */
@media (prefers-reduced-motion: reduce) {
  .wb-row, .wb-kind { transition: none; }
  /* 关掉呼吸动画，但**保留颜色与文字**——标识本身不能因为关动效而消失
     The pulse stops but the badge stays: the marker itself must not depend on motion. */
  .wb-new-badge { animation: none; }
}
"""


def apply(kind_count: int) -> None:
    """
    注入主题 / Inject the theme.

    列宽是**算出来的**而不是写死在 CSS 里：产物种类以后会增减（P6 加音频、
    P7 加播客），写死的话加一列就要同时改 CSS 和 Python，改漏一处表头就和行错位。
    The widths are computed rather than literal: production kinds will come and go, and a
    hard-coded rule would need editing in two places, with a misaligned header as the
    reward for missing one.
    """
    min_width = COL_TITLE_MIN + COL_BODY + COL_MEDIA + COL_KIND * kind_count
    css = (
        _CSS.replace("MIN_TABLE_WIDTH", str(min_width))
        .replace("COL_TITLE_MIN", str(COL_TITLE_MIN))
        .replace("COL_BODY", str(COL_BODY))
        .replace("COL_MEDIA", str(COL_MEDIA))
        .replace("COL_KIND", str(COL_KIND))
        .replace("KIND_COUNT", str(kind_count))
    )
    ui.add_css(css)
    ui.dark_mode().enable()


def short_title(title: str, limit: int = TITLE_MAX_CHARS) -> str:
    """
    截断过长的标题 / Clip an over-long headline.

    CSS 的 `text-overflow: ellipsis` 已经在兜底，这里再截一次是为了**可预测**：
    像素级截断下中文标题露出的字数只有英文的一半，而这张表以中文标题为主。
    CSS ellipsis already guards the layout; clipping here as well makes the result
    predictable, since pixel-based truncation shows half as many characters for the
    Chinese headlines that dominate this table.
    """
    text = (title or "").strip()
    if not text:
        return "(无标题)"
    return text if len(text) <= limit else text[: limit - 1] + "…"


__all__ = ["COL_KIND", "TITLE_MAX_CHARS", "apply", "short_title"]
