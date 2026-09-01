# 08 飞书机器人 + 长连接 部署指南 / Feishu Bot Deployment Guide

> 用途：把 DailyNewsAssistant 的**远程投递接口**部署到你的**私人电脑**，
> 实现「手机上把链接发给机器人 → 自动抽正文入库 → 秒级回执」。
>
> **部署环境说明**：本功能**不在公司电脑运行**。公司电脑只负责开发与离线单元测试（全部用
> mock 事件，不连飞书）；真机联调与长期常驻在你的私人电脑上完成。
>
> 阅读顺序：§1 前置 → §2 飞书后台配置（一次性）→ §3 本机部署 → §4 验证 → §5 常驻运行 → §6 排错

---

## 1. 前置条件

| 项 | 要求 | 说明 |
|---|---|---|
| 飞书账号 | 需属于某个**企业/团队** | 个人号无法建自建应用。没有的话在飞书里免费自建一个团队即可（仅自己一人也行） |
| 角色 | 该团队的**管理员** | 自建团队时你自动就是管理员，可自行审批应用权限 |
| Python | conda `ov_env_py312`（Python 3.12） | 与开发环境一致 |
| 网络 | 机器能**主动出网**访问飞书 | **不需要公网 IP、不需要域名、不需要内网穿透**——长连接是客户端向飞书建 WebSocket |
| 端口 | 无需开放任何入站端口 | 出站 443/WSS 即可 |

---

## 2. 飞书开放平台配置（一次性，约 10 分钟）

打开 [飞书开放平台开发者后台](https://open.feishu.cn/app)。

### 2.1 创建企业自建应用

1. 「创建应用」→ 选择**企业自建应用**
2. 填应用名称（如 `DailyNewsAssistant`）、描述、图标
3. 创建后进入应用详情页

### 2.2 开启机器人能力

1. 左侧「**添加应用能力**」→ 找到「**机器人**」→ 点击「添加」
2. 进入「机器人」配置页，可设置机器人名称与头像（就是你手机上看到的那个）

### 2.3 申请权限

左侧「**权限管理**」，搜索并开通以下权限：

| 权限标识 | 用途 | 必需 |
|---|---|---|
| `im:message.p2p_msg:readonly` | 读取用户发给机器人的**单聊**消息 | ✅ 必需 |
| `im:message:send_as_bot` | 以应用身份**发送消息**（回执） | ✅ 必需 |
| `im:message.group_at_msg:readonly` | 读取群里 **@机器人** 的消息 | 可选（想在群里投递才要） |
| `contact:user.base:readonly` | 获取用户基本信息（回执里显示名字） | 可选 |

> 自建团队的管理员可以直接自我审批通过。

### 2.4 事件订阅改为「长连接」

1. 左侧「**事件与回调**」→「**事件配置**」
2. **订阅方式**选择「**使用长连接接收事件**」（不要选「将事件发送至开发者服务器」——那才需要公网地址）
3. 「添加事件」→ 搜索并添加 **`接收消息 v2.0`**（`im.message.receive_v1`）
4. 如果后续要用交互卡片按钮：「**回调配置**」的订阅方式同样选「**使用长连接接收回调**」，
   并添加 `卡片回传交互`（`card.action.trigger`）

### 2.5 获取应用凭证

左侧「**凭证与基础信息**」→「应用凭证」区域，复制：

- **App ID**（形如 `cli_a1b2c3d4e5f6g7h8`）
- **App Secret**（一长串字符）

⚠️ App Secret 等同密码，**只写进 `.env`，绝不提交到 git**。

### 2.6 发布应用

1. 左侧「**版本管理与发布**」→「创建版本」
2. 填版本号（如 `1.0.0`）、更新说明
3. **可用范围**：选「全部成员」或指定成员（至少要包含你自己）
4. 「申请发布」→ 管理员审批通过（自建团队里就是你自己，去飞书管理后台点通过）

> ⚠️ **未发布的应用，机器人收不到任何消息**——这是最常见的「配置都对但没反应」的原因。

### 2.7 把机器人加为好友

发布后，在飞书客户端搜索你的应用名，把机器人加到通讯录 / 打开单聊窗口。

---

## 3. 私人电脑部署

### 3.1 拉代码与装依赖

```bash
git clone <你的仓库地址> DailyNewsAssistant
cd DailyNewsAssistant

conda activate ov_env_py312
pip install -e .
pip install lark-oapi
```

### 3.2 配置 `.env`

复制 `.env.example` 为 `.env`，填写：

```ini
INBOX_PROVIDER=feishu
INBOX_ENABLED=true
INBOX_SEND_RECEIPT=true

FEISHU_APP_ID=cli_你的AppID
FEISHU_APP_SECRET=你的AppSecret

# 先留空，下一步拿到 open_id 后再填
FEISHU_ALLOWED_USERS=

# 私人电脑通常无代理，务必留空
HTTPS_PROXY=
HTTP_PROXY=
```

⚠️ **`FEISHU_ALLOWED_USERS` 为空时拒收全部消息**——这是刻意的安全默认，防止任何人向你的流水线投递内容。下一步就是拿到自己的 open_id 填进去。

### 3.3 获取你自己的 open_id

最省事的办法是用内置的探测模式：

```bash
python -m dna.inbox.service --print-openid
```

启动后在飞书里给机器人发一条任意消息，控制台会打印：

```
[open_id] ou_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx  (nickname: 你的名字)
```

把这个 `ou_...` 填进 `.env` 的 `FEISHU_ALLOWED_USERS`（多个用逗号分隔），然后 `Ctrl+C` 退出。

> 备选办法：开发者后台「API 调试台」调用 `contact.v3.user.batch_get_id`，用手机号或邮箱换 open_id。

### 3.4 启动

```bash
python -m dna.inbox.service
```

看到下面这行说明长连接建成：

```
connected to wss://open.feishu.cn/callback/ws/endpoint/...
```

---

## 4. 验证清单

在飞书里给机器人发消息，逐条对照：

| # | 你发的内容 | 预期行为 |
|---|---|---|
| 1 | `https://example.com/some-article` | 回执「已入库 1 条：《文章标题》」；`data/dna.db` 的 `items` 表多一行 |
| 2 | 一段话里夹带 2 个链接 | 回执「已入库 2 条」，两条都抽到正文 |
| 3 | `https://xxx #video` | 回执标注「已标记：需制作视频」；该条 `need_video=True` |
| 4 | `https://xxx #en` | 回执标注「已标记：需英文版」 |
| 5 | `https://xxx #skip` | 回执「已忽略」；不入库 |
| 6 | 纯文字，无链接 | 回执「未识别到链接」 |
| 7 | 同一链接发两次 | 第二次回执「已存在，跳过」（跨日去重生效） |
| 8 | 用**另一个账号**给机器人发链接 | **无任何反应**（白名单拦截，且日志记录被拒的 open_id） |

第 8 条是安全底线，务必实测。

---

## 5. 常驻运行（Windows）

### 方式 A：任务计划程序（推荐，够用）

1. 在项目根目录建 `run_inbox.bat`：

```bat
@echo off
call conda activate ov_env_py312
cd /d %~dp0
python -m dna.inbox.service >> logs\inbox.log 2>&1
```

2. 打开「任务计划程序」→ 创建任务
   - 常规：勾「**不管用户是否登录都要运行**」、勾「使用最高权限运行」
   - 触发器：「**登录时**」或「**启动时**」，并勾「如果任务失败，每 1 分钟重启一次，最多 3 次」
   - 操作：启动程序 → `run_inbox.bat`
   - 条件：**取消勾选**「只有在计算机使用交流电源时才启动」（笔记本用电池时也要跑）

### 方式 B：NSSM 注册成 Windows 服务

```bash
nssm install DailyNewsInbox "C:\Users\<你>\miniforge3\envs\ov_env_py312\python.exe" "-m" "dna.inbox.service"
nssm set DailyNewsInbox AppDirectory "C:\path\to\DailyNewsAssistant"
nssm set DailyNewsInbox AppStdout "C:\path\to\DailyNewsAssistant\logs\inbox.log"
nssm start DailyNewsInbox
```

服务方式的好处是开机自启、崩溃自动重拉，不依赖用户登录。

### 与主程序的关系

`inbox` 服务可以独立跑，也可以随 NiceGUI 主程序一起起（`INBOX_ENABLED=true` 时自动在后台线程启动）。

⚠️ **`lark.ws.Client.start()` 会阻塞主线程**，代码里必须放到独立线程，否则会挡住 GUI 和定时调度器。这一点已在 `inbox/feishu_bot.py` 里处理。

---

## 6. 排错对照表

| 现象 | 可能原因 | 处理 |
|---|---|---|
| 启动后没有 `connected to wss://...` | App ID / Secret 填错 | 回后台「凭证与基础信息」核对，注意别把 App ID 和 Secret 弄反 |
| 同上，且报网络错误 | 出网被拦（VPN / 防火墙 / 代理） | 确认 `.env` 里 `HTTPS_PROXY` 为空；关掉 VPN 重试 |
| 连接正常，但发消息机器人无反应 | ①应用未发布 ②未开机器人能力 ③事件未添加 | 依次检查 §2.6 / §2.2 / §2.4，**未发布是最常见原因** |
| 同上，且日志有「rejected sender」 | open_id 不在白名单 | 按 §3.3 拿 open_id 填进 `FEISHU_ALLOWED_USERS` |
| 收到消息但回执发不出 | 缺 `im:message:send_as_bot` 权限 | 补权限后**重新发布版本**（改权限必须重新发版才生效） |
| 群里 @机器人 没反应 | 缺 `im:message.group_at_msg:readonly` | 补权限 + 重新发版 + 把机器人拉进群 |
| 断网恢复后不再收消息 | 重连未生效 | SDK 自带重连；若仍失败，服务方式会自动重拉进程 |
| 链接入库了但正文是空的 | 目标站点反爬或需登录 | 正常现象，`extract` 层会降级为「仅标题 + 链接」，日志里有 warning |
| 改了 `.env` 不生效 | 进程未重启 | 重启服务 |

---

## 7. 安全须知

1. **App Secret 只存 `.env`**，`.gitignore` 已覆盖；一旦泄露立即到后台「重置」
2. **白名单默认空 = 全拒**，不要为了图方便改成放行所有人——机器人是公开可搜的，任何同事都能给它发消息
3. 机器人只做「收链接、抽正文、入库」，**不执行消息里的任何指令性文本**；hashtag 是白名单式解析（只认 `#video` / `#skip` / `#en`），未知 hashtag 一律忽略
4. 抽取外部网页时遵守 robots 与频率限制，配图只记录来源不做二次分发担保

---

## 8. 开发侧约定（公司电脑）

由于本功能在公司电脑上不实际连飞书，开发阶段遵循：

- `inbox/feishu_bot.py` 只负责**协议适配**（收事件 → 转成内部 `InboxMessage` → 发回执），
  业务逻辑全部在 `inbox/service.py`，与飞书 SDK 解耦
- 单元测试用**构造的事件对象**驱动 `service.py`，不依赖网络：
  `tests/inbox/test_inbox_parse.py`、`test_inbox_whitelist.py`、`test_inbox_commands.py`
- 涉及真实长连接的测试标 `@pytest.mark.live`，公司电脑上默认跳过
- 你在私人电脑上联调时如遇问题，把 `logs/inbox.log` 贴回来，按 §6 定位
