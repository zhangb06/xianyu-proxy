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
- 商品上架/改价/擦亮
- 商业分发
- 多机分布式

## 3. 关键决策

| # | 决策 | 结论 |
|---|---|---|
| 1 | 用户模型 | 单管理员 |
| 2 | 管理界面 | 完整运营后台，各领域随阶段交付完整页面，P3 收口 |
| 3 | AI 回复 | P3，OpenAI 兼容单通道 |
| 4 | 部署 | Linux + systemd；Windows 托盘 |
| 5 | 浏览器自动化 | `manual` / `auto_fallback`，全局 + 账号级覆盖，默认 `manual` |
| 6 | 自动化失败 | 转 `verification_required`，UI 展示链接/二维码人工完成；单事件一次尝试并冷却 |
| 7 | 数据迁移 | P0 4 张、P1 5 张、P2 6 张 |
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

### 6.3 P2（6 张）

| 表 | 用途 | 关键约束 |
|---|---|---|
| card | 卡券 | — |
| card_secret | 卡密、占用订单、状态 | `(card_id, secret)` 唯一 |
| card_item | 卡券-商品绑定 | 联合主键 |
| orders | 订单、金额（分）、发货状态 | `(account_id, order_no)` 唯一 |
| delivery_rule | 条件与动作 | `(account_id, code)` 唯一 |
| outbox | 幂等发送队列 | `dedup_key` 唯一，`(status, run_at)` 索引 |

### 6.4 outbox 状态机

```
pending → sending → sent
                  ↘ pending（可重试，指数退避）
                  ↘ dead（超过最大次数）
```

启动时将 `sending` 恢复为 `pending`。`dedup_key` 由业务决定，例如 `deliver:{order_no}`、`reply:{msg_dedup}`。

### 6.5 卡密状态机

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

### 7.5 标识生成

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
| 订单 | 列表、状态、发货状态、重发 | P2 |
| 卡密 | 卡片、库存、批量导入、商品绑定 | P2 |
| 发货规则 | 条件、动作、优先级 | P2 |
| 设置 | 全局配置、验证模式、日志级别、备份 | P3 |
| 日志 | 事件检索、脱敏展示 | P3 |

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

### P2 自动发货 + 订单域 UI

- 内容：订单事件 + 周期对账、发货规则、卡密库存、原子领取、outbox 重试；订单/卡密/规则页面
- 验收：同一订单并发只发一次；断网恢复后补发；库存不超发；失败可人工重派

### P3 AI + 设置/日志/统计

- 内容：OpenAI 兼容单通道（超时、限额、失败回退默认回复）、事件日志、统计、备份
- 验收：AI 故障不影响基础回复；页面可完成全部配置

### P4 打包与守护

- 内容：systemd 单元、Windows 托盘、安装脚本
- 验收：全新机器按文档一次安装成功

### P5 加固

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
