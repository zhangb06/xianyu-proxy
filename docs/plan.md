# xianyu-lite 重构方案

- 状态：v2，已通过架构自评审（2026-11-03）
- 分支：`feat/lite-rewrite`（集成分支，阶段完成后合入 `main`）
- 流程：方案评审 → 实现 → 验收，`main` 不接收未评审代码

## 0. 评审结论

v1 的方向成立，但存在 4 个会导致线上事故的硬伤和 12 个实现级缺口。本版已全部修正：

| # | 问题 | 级别 | 处理 |
|---|---|---|---|
| R1 | 2C2G 与浏览器验证内存冲突 | 高 | 预算拆分；默认 `manual`；`auto_fallback` 仅在 ≥4C4G 或配置 swap 时启用；单并发 + 120 秒硬超时 |
| R2 | “单写者”没有实现机制，并发写会锁冲突 | 高 | 全局写锁 + `BEGIN IMMEDIATE`；读写共用单连接，事务短小 |
| R3 | 自发消息回显可能触发自动回复 | 高 | 出站先落库；`sender == self` 一律不进回复管道；回显只做状态对账 |
| R4 | 卡密领取后发送失败会造成库存黑洞 | 高 | 领取与发送分离为 `claimed → sent / failed`；失败释放或转人工重派 |
| R5 | `msg_id` 缺失时去重失效 | 中 | `dedup_key` 回退：`sha1(chat_id|sender|ts|content)` |
| R6 | 进程崩溃时 outbox 卡在 `sending` | 中 | 启动恢复 `sending → pending`，退避重试，超限转 `dead` |
| R7 | 仅靠消息事件会漏订单 | 中 | 事件触发 + 周期对账双通道 |
| R8 | 没有发送频控，易触发风控 | 中 | 每账号令牌桶 + 最小发送间隔 + 随机抖动 |
| R9 | 管理端认证细节缺失 | 中 | 首次启动强制设置口令；PBKDF2 存储；`SameSite=Strict` + CSRF token |
| R10 | 日志可能泄露 Cookie/Token | 中 | 统一脱敏：cookie、token、手机号、地址 |
| R11 | 消息与事件无限增长 | 中 | 保留期 + 定期清理 + `VACUUM` |
| R12 | Windows 托盘实现未定 | 中 | `pystray` 可选 extra；单实例锁；端口冲突探测 |
| R13 | 部署缺少加固 | 中 | 专用用户、`NoNewPrivileges`、自动重启、环境文件 0600 |
| R14 | 测试策略不足 | 中 | 单测 + 假 WS 集成测试 + CI 禁网 |
| R15 | 金额与时区未约定 | 低 | 金额存整数分；时间统一 UTC，前端本地化 |
| R16 | 依赖不可复现 | 低 | 提交 lock 文件，生产环境不装 dev extra |

## 1. 背景与目标

现有系统功能完整但资源占用高：多进程 + MySQL + Redis + 浏览器运行时，官方基线 2C4G / 4C8G。

本次独立重写，只保留三个核心域：多账号托管、消息与自动回复、自动发货。

目标：2C2G Linux 稳定运行；Windows 提供托盘版，面板用浏览器打开。

## 2. 范围

### 2.1 包含

- 单管理员，首次启动设置口令
- 多账号扫码登录、Cookie 加密存储、账号级代理、连接与重连
- 消息接收、去重、落库、关键词与默认回复、防抖、黑名单、单聊暂停
- 文本/图片消息处理；语音仅占位；卡片消息不触发回复
- 订单同步（事件 + 对账）、发货规则、卡密库存、原子领取、失败重试
- 完整运营后台：账号、规则、会话、订单、卡密、发货规则、设置、日志
- 可选浏览器验证与人工兜底
- Linux systemd；Windows 托盘

### 2.2 不包含

- 多租户、注册、计费、激活码
- 商品上架/改价
- 商业分发
- 多机分布式

## 3. 关键决策

| # | 决策 | 结论 |
|---|---|---|
| 1 | 用户模型 | 单管理员 |
| 2 | 管理界面 | 完整运营后台，各领域随阶段交付完整页面，P4 收口 |
| 3 | AI 回复 | P2，OpenAI 兼容单通道（默认 DeepSeek），AI 优先、关键词兜底 |
| 4 | 部署 | Linux + systemd；Windows 托盘 |
| 5 | 浏览器自动化 | `manual` / `auto_fallback`，全局 + 账号级覆盖，默认 `manual` |
| 6 | 自动化失败 | 转 `verification_required`，UI 展示链接/二维码人工完成；单事件一次尝试并冷却 |
| 7 | 数据迁移 | P0 4 张、P1 5 张、P2 2 张、P3 6 张 |
| 8 | 前端 | React + Vite + Ant Design，静态产物由 FastAPI 托管 |
| 9 | 浏览器实现 | Playwright 可选 extra，默认不装 Chromium |
| 10 | 旧代码 | 仅作协议事实参考，正式实现独立重写 |
| 11 | 许可 | 新仓库不商用，不引入旧仓库 AGPL 代码 |

## 4. 总体架构

单进程 asyncio 模块化单体，一个 uvicorn worker，生产禁用 reload。

| 组件 | 职责 |
|---|---|
| Supervisor | 账号编排、并发上限、状态机、崩溃重启、优雅退出 |
| AccountActor | 单账号连接、注册、心跳、重连、Token 刷新、消息分发 |
| Pipeline | 去重 → 落库 → 分类 → 规则匹配 → 回复决策 |
| Delivery | 订单事件/对账 → 规则 → 卡密领取 → outbox |
| Outbox | 延迟、重试、幂等发送 |
| API | 后台与内部接口 |
| Static | 前端构建产物，同进程托管 |

### 4.1 关键机制

- 无 Redis：延迟与重试落在 `outbox`
- 数据库写：`asyncio.Lock` 串行化 + `BEGIN IMMEDIATE`；禁止跨网络等待持有事务
- 卡密领取：事务内 `SELECT` 可用卡密并 `UPDATE` 占用，失败回滚
- 账号隔离：单账号故障不影响其他 Actor；指数退避 + 抖动
- 协议隔离：报文、签名、设备 ID 收敛在 `app/protocol/`
- 发送频控：每账号令牌桶（默认 1 条/2 秒）+ 0–800ms 抖动
- 出站优先落库：先写 `message(direction=out)` 与 outbox，再发送

### 4.2 账号状态机

```
new → connecting → online
              ↘ reconnecting → online
online → verification_required（滑块/人脸）
online → paused（人工或 AI 暂停）
online → error（认证失败/连续失败超限）
任意 → disabled（管理员停用）
```

Supervisor 对 Actor 崩溃自动重启，采用指数退避；连续 5 次失败转 `error`。

### 4.3 关停顺序

停止接新消息 → 取消 Actor → 心跳与 WS 关闭 → `sending` 状态回写 → 写锁内 checkpoint → 关闭数据库。

## 5. 目录结构

```
app/
  main.py       进程入口与编排
  config.py     XY_ 配置
  api/          后台与内部 HTTP
  core/         加密、ID、日志、时间、脱敏
  platform/     Linux/Windows 路径、托盘、服务安装
  db/           engine、migrations、repo
  protocol/     mtop、token、扫码、WS、消息解析
  runtime/      Supervisor、AccountActor
  pipeline/     去重、规则、回复
  delivery/     订单、卡密、outbox
  browser/      可选验证适配器
  web/          前端源码与构建
scripts/        冒烟与运维
tests/          单测与 fixture
docs/           方案与运维文档
```

## 6. 数据模型

时间统一 UTC ISO-8601；金额存整数分。

### 6.1 P0（4 张）

| 表 | 用途 | 关键约束 |
|---|---|---|
| setting | 全局配置、管理员口令哈希、加密密钥 | `key` 主键 |
| account | 账号、状态、设备、代理、验证模式 | `xianyu_id` 唯一 |
| account_credential | Cookie/Token（AES-GCM） | `(account_id, kind)` 主键 |
| event_log | 运行事件 | `(created_at)`、`(account_id, type)` 索引 |

### 6.2 P1（5 张）

| 表 | 用途 | 关键约束 |
|---|---|---|
| chat | 会话、关联商品、暂停时间 | `(account_id, chat_id)` 唯一 |
| message | 收发消息、去重键 | `(account_id, msg_id)` 唯一，`dedup_key` 索引 |
| keyword_rule | 关键词规则 | 账号 + 优先级 |
| reply_config | 默认回复、防抖、过期 | 账号主键 |
| block_entry | 黑名单（用户/商品/关键词） | `(account_id, kind, value)` 唯一 |

### 6.3 P2 AI 回复（2 张）

| 表 | 用途 | 关键约束 |
|---|---|---|
| ai_config | 渠道、模型、API Key（加密）、人设、商品说明、底价、上下文条数 | 账号主键 |
| ai_usage | 调用记录、耗时、token、结果状态 | `(account_id, created_at)` 索引 |

### 6.4 P3 自动发货（6 张）

| 表 | 用途 | 关键约束 |
|---|---|---|
| card | 卡券 | — |
| card_secret | 卡密、占用订单、状态 | `(card_id, secret)` 唯一 |
| card_item | 卡券-商品绑定（旧版兼容） | 联合主键 |
| orders | 订单、金额（分）、发货状态 | `(account_id, order_no)` 唯一 |
| delivery_rule | 条件与动作 | `(account_id, code)` 唯一 |
| outbox | 幂等发送队列 | `dedup_key` 唯一，`(status, run_at)` 索引 |

- 订单准入：只接受整条方括号包裹的闲鱼系统通知，或携带真实订单号（≥10 位数字）的消息；
  买家手打的「我已付款」「你已发货」等文本不会创建订单、不会自动发货标记，仅记录 `order.untrusted_notice` 事件
- 通知去重：无真实订单号时，同一会话同商品在 120 秒内的付款通知只建一单；
  「记得及时发货」仅在会话内没有任何订单时用于补建，避免与付款通知重复发卡

### 6.5 P3 商品目录与按商品发货（2 张）

| 表 | 用途 | 关键约束 |
|---|---|---|
| item | 已上架商品缓存（标题/价格/状态/封面/同步时间） | `(account_id, item_id)` 唯一 |
| item_delivery | 单商品发货配置（模式/卡密池/模板/内容/自动标记） | `(account_id, item_id)` 唯一 |

- 同步：商品页手动同步 + 后台定时同步（默认 10 分钟，`XY_CATALOG_SYNC_SECONDS` 可调，0 关闭）；
  完整同步后未出现的商品标记已下架；新上架/下架/售出状态自动更新
- 页面只展示在售商品，已售出/已下架仅保留数据供订单查询
- 发货模式：`card`（卡密池 + 模板）、`fixed`（固定内容）；未配置或显式关闭即人工处理（`manual` 仅保留兼容历史配置）
- 变量：页面仅提供 `{secret}`、`{order_no}`、`{item_title}`、`{buyer}`；后端仍兼容 `{chat_id}`、`{price}`
- 默认关闭：商品发货配置新建时 `enabled=false`，显式开启并保存后才自动发送
- 优先级：启用的商品配置 → 按模式执行；显式关闭 → 转人工；无配置 → 仅当旧版卡券绑定与启用规则同时存在时兜底，否则人工

### 6.6 outbox 状态机

```
pending → sending → sent
                  ↘ pending（可重试，指数退避）
                  ↘ dead（超过最大次数）
```

启动时将 `sending` 恢复为 `pending`。`dedup_key` 由业务决定，例如 `deliver:{account_id}:{order_no}`、`reply:{msg_dedup}`。

### 6.7 卡密状态机

```
available → claimed（订单占用） → used（发送成功）
                              ↘ available（发送失败释放）
                              ↘ manual（多次失败转人工）
```

## 7. 协议层

### 7.1 扫码登录

- 参数页：`passport.goofish.com/mini_login.htm`
- 二维码：`newlogin/qrcode/generate.do`
- 轮询：`newlogin/qrcode/query.do`，状态 `NEW / SCANED / CONFIRMED / EXPIRED`
- `iframeRedirect` 为真：`verification_required`，UI 展示验证地址或二维码
- 入口区分：顶部「扫码添加」不带账号，按 `unb` upsert；账号行「重新扫码」带 `account_id`，
  扫码得到的 `unb` 与该账号不一致时拒绝写入 Cookie 并提示，避免误覆盖或误建账号

### 7.2 IM Token

- API：`mtop.taobao.idlemessage.pc.login.token`
- 请求体：IM appKey + deviceId
- 签名：`md5(token & t & appKey & data)`
- 失效处理：重取签名重试一次；仍失败转 `error`，等待重新扫码
- Cookie 随每次 mtop 响应增量更新，不设独立 Cookie 轮询任务

### 7.3 WebSocket

- 地址：`wss://wss-goofish.dingtalk.com/`
- 注册 `/reg` → 同步位点 `/r/SyncStatus/ackDiff`
- 心跳 `/!`，默认 15 秒；连续失败 3 次断开重连
- 收帧 ACK（`code=200`，回填 `mid/sid`）
- 同步包：`syncPushPackage.data[].data` 为 base64（JSON 或 MessagePack）
- 发送：`/r/MessageSend/sendByReceiverScope`
- 按 `headers.mid` 关联响应，提取拦截原因

### 7.4 消息去重

- 优先使用协议消息 ID：`(account_id, msg_id)` 唯一
- 缺失时使用 `dedup_key = sha1(chat_id|sender|timestamp|content)`，同一账号内唯一
- 自发消息：`sender == self` 时只落库与对账，不进入回复管道

### 7.5 商品列表

- API：`mtop.idle.web.xyh.item.list` v1.0（POST，签名同其他 mtop）
- 请求体：`needGroupInfo`、`pageNumber`、`pageSize`、`userId`（闲鱼 ID）
- 响应：`data.cardList[].cardData`（`id/title/itemStatus/priceInfo/picInfo`），`nextPage` 控制翻页
- 状态码：`0` 在售、`1` 已售出、`2` 已下架；单次同步最多 20 页

### 7.6 标识生成

- `mid`：随机前缀 + 毫秒时间戳
- `uuid`：时间戳派生
- `device_id`：36 位 + 校验位 + `-` + 用户 ID，与原系统互通

## 8. 运营后台

| 页面 | 内容 | 阶段 |
|---|---|---|
| 首次设置 | 设置管理员口令（仅本机可访问） | P1 |
| 登录 | 口令登录、会话 Cookie | P1 |
| 总览 | 在线账号、今日收发、发货失败 | P1 |
| 账号 | 列表、扫码登录、状态、启停、代理、验证模式 | P1 |
| 规则 | 关键词、默认回复、防抖/过期、黑名单 | P1 |
| 会话 | 列表、消息记录、手动回复、暂停 | P1 |
| AI 回复 | 渠道配置、人设/商品说明、底价、上下文条数、测试 | P2 |
| 商品 | 仅展示在售商品、状态/价格/封面、按商品配置自动发货 | P3 |
| 订单 | 列表、状态、发货状态、重发 | P3 |
| 卡密 | 卡片、库存、批量导入 | P3 |
| 发货规则 | 旧版条件/动作绑定、未配置商品的兜底 | P3 |
| 设置 | 全局配置、验证模式、日志级别、备份 | P4 |
| 日志 | 事件检索、脱敏展示 | P4 |

- 前端为 SPA 静态产物，开发期 Vite 代理，生产由 FastAPI 托管
- 登录使用签名 Cookie：`HttpOnly`、`Secure`（启用 HTTPS 时）、`SameSite=Strict`
- 所有写接口校验 CSRF token 与 `Origin`

## 9. 阶段与验收

每阶段完成定义：代码 + 单测 + 文档 + 资源实测记录。

### P0 协议验证（spike，无 UI）

- 内容：扫码 → Cookie 加密落库 → 连 WS → 收一条 → 发一条
- 交付：4 张表、协议层、CLI 冒烟脚本
- 验收：真实账号完成四步；连续在线 30 分钟；同步包录制为 fixture
- 说明：CLI 打印 ASCII 二维码并保存 PNG，适配无图形界面的 Linux
- spike 代码在 P1 可被运行时替换，不作为长期接口

### P1 运行时 + 自动回复 + 账号域 UI

- 内容：Supervisor/AccountActor、多账号、代理、去重、关键词与默认回复、防抖、黑名单、暂停、验证状态展示；账号/规则/会话/总览页面
- 验收：3 账号并行；回复延迟 < 3 秒；重启不重复回复；UI 覆盖账号域全部操作

### P2 AI 智能回复 + 规则页 AI 配置

- 内容：OpenAI 兼容单通道（默认 DeepSeek），每账号配置渠道/人设/商品说明/底价/上下文条数；
  AI 优先、关键词与默认话术兜底；超时、限额、连续失败自动降级；会话人工接管
- 上下文：近 N 条会话记录 + 账号级商品说明与人设；接入商品详情接口，回复前读取标题/描述/价格作为事实依据
- 验收：砍价不突破底价；AI 故障/超时不影响基础回复；回复延迟 < 8 秒；
  页面可完成全部 AI 配置并测试连通性

### P3 自动发货 + 商品目录 + 订单域 UI

- 内容：商品目录同步（真实商品列表）、按商品的卡密池/固定内容/人工三种发货模式、
  订单事件 + 周期对账、旧版发货规则兜底、卡密库存、原子领取、outbox 重试；
  商品/订单/卡密/规则页面
- 验收：同一订单并发只发一次；断网恢复后补发；库存不超发；失败可人工重派；
  商品页可见已上架商品并逐商品配置生效

### P4 设置/日志/统计

- 内容：全局配置、事件检索、统计、备份
- 验收：页面可完成全部配置；日志可检索且脱敏

### P5 打包与守护

- 内容：systemd 单元、Windows 托盘、安装脚本
- 验收：全新机器按文档一次安装成功

### P6 加固

- 内容：2C2G 压测、7 天浸泡、重连风暴、故障注入
- 验收：10 账号 + 1 msg/s 峰值 < 700MB（不含浏览器），无内存泄漏，无重复发货

## 10. 资源预算

| 项 | 预算 |
|---|---|
| 进程基线 | 60–90 MB |
| 每账号 | 3–6 MB |
| 消息与队列缓冲 | < 100 MB |
| 空闲目标 | 150–300 MB |
| 服务峰值（不含浏览器） | < 700 MB |
| 浏览器验证瞬时峰值 | ≤ 1.2 GB，单并发，120 秒超时 |

2C2G 默认 `manual`；启用 `auto_fallback` 需 ≥4C4G 或配置 swap，且验证期间不并发其他重任务。

## 11. 部署形态

- Linux：单 systemd 服务，专用用户，数据目录 `/var/lib/xianyu-lite`
  - `NoNewPrivileges=true`、`PrivateTmp=true`、`ProtectSystem=strict`
  - 环境文件权限 0600，`Restart=on-failure`
- Windows：`pystray` 托盘进程内启动 HTTP 服务
  - 单实例锁；端口占用时提示并退出；菜单打开浏览器
  - 数据目录 `%LOCALAPPDATA%\XianyuLite`
- 配置：`XY_` 环境变量 + `.env`

## 12. 安全

- Cookie/Token：AES-GCM；密钥来自 `XY_SECRET_KEY`，未配置时生成一次写入 `setting`
- 密钥丢失即凭据不可解，安装时提示备份 `setting.secret_key`
- 管理员口令：PBKDF2-HMAC-SHA256 + 随机盐，存 `setting`
- 首次启动：无口令时仅允许本机访问设置页，设置完成前不开放其他接口
- 日志脱敏：cookie、token、手机号、详细地址
- 默认监听 `127.0.0.1`，对外暴露需显式配置并置于反向代理之后

## 13. 数据保留与备份

- `message` 默认保留 90 天，`event_log` 默认 30 天，可配置
- 清理任务每日执行，批量删除后按需 `VACUUM`
- 备份使用在线快照（`VACUUM INTO`），保留最近 7 份

## 14. 风险与对策

| 风险 | 对策 |
|---|---|
| 协议变更/风控 | 协议层隔离 + fixture 回放；验证转人工 |
| Cookie/Token 失效 | 刷新失败转 `error` 并提示重新扫码 |
| SQLite 锁冲突 | 写锁 + `BEGIN IMMEDIATE` + 短事务 |
| 订单漏事件 | 事件触发 + 周期对账 |
| 重复发货 | 订单级幂等 + 卡密状态机 + outbox 去重 |
| 浏览器资源 | 可选依赖、单并发、硬超时、默认关闭 |
| 旧代码许可 | 不复制实现，仅按接口事实重写 |
| Windows 兼容 | 平台层隔离；不使用 fork |

## 15. 测试策略

- 单测：签名向量、ID 形态、消息解析、去重键、卡密领取、outbox 状态机
- 集成：本地假 WS 服务回放 fixture，覆盖注册/心跳/ACK/同步包/发送响应
- CI：禁止真实网络；fixture 驱动；`ruff` + `pytest`
- 手工验收：真实账号扫码与收发，仅在阶段验收时执行

## 16. 流程约定

- `main` 不接收未评审实现
- 实现提交在 `feat/lite-rewrite`，阶段验收后合入 `main`
- 方案变更先改本文件并自评审
- 每阶段提交资源实测数据与 fixture

## 17. 暂缓项（不阻塞）

- 代理池与自动切换（P1 先支持账号级单代理）
- 指标上报（P5 评估）
- 多语言（不做）
- 密钥轮换（文档提示备份，功能不做）

## 18. P7 商品擦亮与要小红花（自评审通过，2026-11-09）

### 18.1 范围与默认行为

- 纳入商品擦亮与发货后的小红花请求；不做商品上架/改价，不绕过闲鱼的资格、频控或风控限制。
- 两项自动化默认关闭，账号停用/未登录时不执行；擦亮只针对在售商品。
- 要小红花是发货后的 IM 文本请求，不是自动赠送小红花；不宣称买家已评价或已赠送，除非后续拿到可验证的平台事件。

### 18.2 擦亮

- 在 `app/protocol/` 独立封装 `mtop.taobao.idle.item.polish`：URL 版本段 `1.0`、query 参数 `v=2.0`（现有 `mtop_call` 的 URL 与 `v` 共用一个版本值，须用 `extra_params={"v": "2.0"}` 覆盖），请求体仅 `itemId`；复用现有 Cookie、代理、签名与令牌过期重试。
- `SUCCESS`、`IDLEITEM_POLISH_AGAIN`、`POLISH_DUPLICATE` 及明确的“已经擦亮/一天只能擦亮一次”返回均视为当天已完成；`UNSUPPORTED_ITEM_STATUS`（已下架）不重试并等待下次商品同步纠正状态，其他错误透出原因。
- 每日额度按**北京时间自然日**判断（与原系统日切一致），不是滚动 24 小时：`polished_at` 存 UTC，比较时换算 Asia/Shanghai 日期；跨天后允许再次擦亮。
- 商品页提供单商品立即擦亮按钮与最近擦亮时间/失败原因；账号级自动擦亮开关放在账号编辑弹窗。
- 后台在 Supervisor 增加常驻擦亮循环（每 300 秒一次轻量查询，无账号开启时立即跳过）：遍历在售且当天未擦亮的商品，同账号请求间隔 ≥2 秒；认证类错误按账号退避，不连续重试。
- 手动擦亮与自动擦亮共享 PolishService 的每账号锁和当天完成判断；当天已擦亮时手动接口返回“今天已擦亮”而非报错；失败不伪造成功时间；Cookie 有变化时写回账号凭据。

### 18.3 要小红花

- 按已确认的产品语义，做发货后的自动 IM 私聊请求；不调用原生 `mtop.taobao.idlemessage.red.flower` 订单接口，也不自动赠送小红花。
- 账号级开关放在账号编辑弹窗，默认关闭；仅在订单发货内容**实际发送成功**后触发（人工标记已发货、平台发货通知不触发）。
- 发货成功后等待 30 分钟，经现有 outbox 发送一次固定话术：“如果这次交易对你有帮助，方便的话请送一朵小红花，谢谢～”。每账号每订单唯一去重键，重启不重复、发送失败按现有退避重试。
- 执行时复查：功能仍开启、订单仍为 delivered、会话未暂停、买家未被拉黑，任一不满足标记 skipped 不发送；账号离线/发送失败走现有退避重试，重试超限标记 failed。消息先落库后发送（`insert_outbound` 已支持同 dedup_key 复用原行），复用账号 WS 频控。
- worker 按 `task` 字段分流：`flower` 任务走独立执行与失败路径，只更新 orders 的小红花列，绝不改动订单发货状态、卡密或触发 `fail_permanently` 的转人工逻辑。
- 订单页显示请求状态与失败原因；不将普通文本或不可验证的通知当作“已给小红花”。

### 18.4 数据与接口落点（迁移 007）

- 新表 `account_ops`（account_id 主键）：`auto_polish`、`flower_enabled`，默认关闭；API `GET/PUT /accounts/{id}/ops`。
- `item` 增列：`polished_at`、`polish_error`；API `POST /accounts/{id}/items/{item_id}/polish`；商品列表 payload 携带两列。
- `orders` 增列：`flower_status`（空/pending/sent/skipped/failed）、`flower_at`、`flower_error`；outbox 新任务类型 `flower`，去重键 `flower:{account_id}:{order_no}`；订单 payload 携带三列。
- 常量而非新增配置项：循环间隔 300 秒、同账号商品间隔 2 秒、小红花延迟 1800 秒、重试沿用 outbox 现有上限。
- 手动擦亮只要求账号有 Cookie（HTTP 接口独立于 WS 在线状态）；自动化循环只处理启用且有 Cookie 的账号。

### 18.5 验收与协议前置条件

- 自动化测试覆盖擦亮协议响应、当天完成/冷却/额度错误、北京时间跨日边界、在售过滤、手动/自动互斥、默认关闭、outbox 幂等、延迟、执行时复查（黑名单/暂停/订单状态）、重启恢复；执行后端全量测试、前端全量测试与构建。
- 擦亮接口事实已由只读协议核对确认；真实验收仍须在有效登录态下擦亮一次，并在闲鱼端核对结果。用测试订单确认小红花私聊请求仅发送一次。当前服务器账号仍因 IM Token 失效处于 `error`，须重新扫码后才可进行该项验收。
- 通过本地/隔离环境验收后再部署；不擅自重启正在使用的本地 8089 或服务器服务。

### 18.6 自评审记录（2026-11-09）

- 已核对复用点真实存在：`mtop_call` 支持 `extra_params` 覆盖 `v`；`insert_outbound` 对同 dedup_key 复用原消息行（重试不产生重复记录）；`BlockRepo.is_blocked`、`Chat.reply_paused`、`ChatRepo.get(account_id, chat_id)`、`CookieJar.as_dict`、`AccountRepo.save_cookie` 均可直接使用。
- 修正：滚动 24 小时改为北京时间自然日；`v=2.0` 与 URL `1.0` 分离；worker 对 `flower` 任务独立分流，失败不污染订单/卡密状态；离线走重试而非跳过。
- 风险与对策：擦亮接口风控未知 → 默认关闭、每天一次、间隔节流、错误退避；小红花打扰买家 → 默认关闭、延迟 30 分钟、每单一次、执行时复查黑名单/暂停。
- 结论：方案可实施；真机验收依赖重新扫码（当前服务器账号 IM Token 失效）。
