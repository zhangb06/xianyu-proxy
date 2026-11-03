# xianyu-lite 重构方案

- 状态：已评审（2026-11-03）
- 分支：`feat/lite-rewrite`（集成分支，阶段完成后合入 `main`）
- 原则：方案先评审，评审通过后才落地实现

## 1. 背景与目标

现有系统（`xianyu-auto-reply`）功能完整，但资源占用高：多进程 + MySQL + Redis + 浏览器运行时，官方基线为 2C4G / 4C8G。

本次以独立仓库重写，只保留三个核心域：

1. 多账号托管
2. 消息与自动回复
3. 自动发货

目标：在 2C2G Linux 上稳定运行，同时提供 Windows 托盘版（面板用浏览器打开）。

## 2. 范围

### 2.1 包含

- 单管理员
- 多账号扫码登录、Cookie 加密存储、连接与重连
- 消息接收、去重、落库、关键词与默认回复、防抖、黑名单、单聊暂停
- 订单同步、发货规则、卡密库存、卡密原子领取、发货重试
- 完整运营后台（账号、规则、会话、订单、卡密、发货规则、设置、日志）
- 可选浏览器验证（滑块/人脸）与人工兜底
- Linux systemd 部署；Windows 托盘 + 浏览器面板

### 2.2 不包含

- 多租户、注册、计费、激活码
- 商品上架/改价/擦亮等运营工具
- 商业授权与分发
- 多机分布式部署

## 3. 关键决策

| # | 决策 | 结论 |
|---|---|---|
| 1 | 用户模型 | 单管理员 |
| 2 | 管理界面 | 完整运营后台，按阶段交付各领域完整页面 |
| 3 | AI 回复 | P3 接入，OpenAI 兼容单通道 |
| 4 | 部署 | Linux + systemd 为主；Windows 托盘版 |
| 5 | 浏览器自动化 | 可选：`manual` 或 `auto_fallback`，全局配置 + 账号级覆盖，默认 `manual` |
| 6 | 自动化失败 | 转 `verification_required`，UI 展示链接/二维码，人工完成；单事件只尝试一次并冷却 |
| 7 | 数据迁移 | 分三批：P0 4 张、P1 5 张、P2 6 张 |
| 8 | 前端 | React + Vite + Ant Design，静态产物由 FastAPI 托管 |
| 9 | 浏览器实现 | Playwright 作为可选 extra，默认不安装 Chromium |
| 10 | 旧代码 | 仅作参考，正式实现独立重写 |
| 11 | 许可 | 新仓库不商用，不引入旧仓库 AGPL 代码 |

## 4. 总体架构

单进程 asyncio 模块化单体，一个 uvicorn worker。

| 组件 | 职责 |
|---|---|
| Supervisor | 账号任务编排、并发上限、状态机、优雅退出 |
| AccountActor | 单账号：连接、注册、心跳、重连退避、Token 刷新、消息分发 |
| Pipeline | 消息去重 → 落库 → 规则匹配 → 回复决策 |
| Delivery | 订单同步 → 发货规则 → 卡密原子领取 → 写 outbox |
| Outbox | 延迟、重试、幂等发送（唯一 `dedup_key`） |
| API | 运营后台与内部接口 |
| Static | 前端构建产物，同进程托管 |

### 4.1 关键取舍

- 无 Redis：延迟与重试落在 SQLite `outbox` 表
- 单写者：全部写操作走同一连接，事务短小，WAL + `busy_timeout`
- 卡密原子领取：单事务内选中并占用，失败回滚，不超发
- 账号隔离：单账号故障不影响其他 Actor，指数退避 + 抖动避免重连风暴
- 协议隔离：报文格式、签名、设备 ID 收敛在 `app/protocol/`，上层不感知

### 4.2 账号状态机

```
new → connecting → online
              ↘ reconnecting → online
online → paused（人工/AI 暂停、验证中止）
online → error（认证失败、连续失败）
任意 → disabled（管理员停用）
```

## 5. 目录结构

```
app/
  main.py       进程入口与任务编排
  config.py     配置（XY_ 前缀）
  api/          后台与内部 HTTP
  core/         加密、ID、日志、时间
  platform/     Linux/Windows 路径、托盘、服务安装
  db/           engine、migrations、repo
  protocol/     mtop、token、扫码、WS、消息解析
  runtime/      Supervisor、AccountActor、重连
  pipeline/     去重、规则匹配、回复决策
  delivery/     订单、卡密、发货规则、outbox
  browser/      可选验证适配器（子进程）
  web/          前端源码与构建
scripts/        冒烟与运维脚本
tests/          单测与协议 fixture
docs/           方案与运维文档
```

## 6. 数据模型

### 6.1 P0（4 张）

| 表 | 用途 | 关键约束 |
|---|---|---|
| setting | 全局配置与密钥 | `key` 主键 |
| account | 账号 | `xianyu_id` 唯一 |
| account_credential | Cookie 等凭据（AES-GCM） | `(account_id, kind)` 主键 |
| event_log | 运行事件 | 时间索引 |

### 6.2 P1（5 张）

| 表 | 用途 | 关键约束 |
|---|---|---|
| chat | 会话 | `(account_id, chat_id)` 唯一 |
| message | 收发消息 | `(account_id, msg_id)` 唯一 |
| keyword_rule | 关键词规则 | 账号 + 优先级索引 |
| reply_config | 回复默认配置 | 账号主键 |
| block_entry | 黑名单 | `(account_id, kind, value)` 唯一 |

### 6.3 P2（6 张）

| 表 | 用途 | 关键约束 |
|---|---|---|
| card | 卡券 | — |
| card_secret | 卡密 | `(card_id, secret)` 唯一 |
| card_item | 卡券-商品绑定 | 联合主键 |
| orders | 订单 | `(account_id, order_no)` 唯一 |
| delivery_rule | 发货规则 | `(account_id, code)` 唯一 |
| outbox | 幂等发送队列 | `dedup_key` 唯一 |

迁移按 `PRAGMA user_version` 顺序执行，文件名 `NNN_*.sql`。

## 7. 协议层

### 7.1 扫码登录

- 参数页：`passport.goofish.com/mini_login.htm`
- 二维码：`newlogin/qrcode/generate.do`
- 轮询：`newlogin/qrcode/query.do`，状态 `NEW / SCANED / CONFIRMED / EXPIRED`
- `iframeRedirect` 为真：进入人脸验证分支，UI 展示验证地址或二维码

### 7.2 IM Token

- API：`mtop.taobao.idlemessage.pc.login.token`
- 请求体携带 IM appKey 与 deviceId
- 签名：`md5(token & t & appKey & data)`
- Token 失效：重新取签名重试一次，仍失败则账号转 `error`，等待重新扫码

### 7.3 WebSocket

- 地址：`wss://wss-goofish.dingtalk.com/`
- 注册 `/reg` → 同步位点 `/r/SyncStatus/ackDiff`
- 心跳 `/!`，默认 15 秒，连续失败 3 次断开重连
- 收帧即 ACK（`code=200` + `mid/sid`）
- 同步包：`syncPushPackage.data[].data` 为 base64（JSON 或 MessagePack）
- 发送：`/r/MessageSend/sendByReceiverScope`
- 发送结果：按 `headers.mid` 关联响应，提取拦截原因

### 7.4 标识生成

- `mid`：随机前缀 + 毫秒时间戳
- `uuid`：时间戳派生
- `device_id`：36 位 + 校验位 + `-` + 用户 ID，算法与原系统保持互通

## 8. 运营后台页面

| 页面 | 内容 | 阶段 |
|---|---|---|
| 登录 | 单管理员口令 | P1 |
| 总览 | 账号在线数、今日收发、发货失败 | P1 |
| 账号 | 列表、扫码登录、状态、启停、代理、验证模式 | P1 |
| 规则 | 关键词、默认回复、防抖/过期、黑名单 | P1 |
| 会话 | 会话列表、消息记录、手动回复、暂停 | P1 |
| 订单 | 列表、状态、发货状态、重发 | P2 |
| 卡密 | 卡片、库存、批量导入、商品绑定 | P2 |
| 发货规则 | 条件、动作、优先级 | P2 |
| 设置 | 全局配置、验证模式、日志级别、备份 | P3 |
| 日志 | 事件检索 | P3 |

前端为 SPA 静态产物，由 FastAPI 同进程托管；开发期 Vite 代理到后端。

## 9. 阶段与验收

### P0 协议验证（无 UI）

- 内容：扫码 → Cookie 加密落库 → 连 WS → 收一条 → 发一条
- 验收：真实账号完成四步；连续在线 30 分钟；同步包录制为 fixture

### P1 运行时 + 自动回复 + 账号域 UI

- 内容：Supervisor/AccountActor、多账号、去重、关键词与默认回复、防抖、黑名单、暂停；账号/规则/会话/总览页面
- 验收：3 账号并行；回复延迟 < 3 秒；重启不重复回复；UI 覆盖账号域全部操作

### P2 自动发货 + 订单域 UI

- 内容：订单同步、发货规则、卡密库存、原子领取、outbox 重试；订单/卡密/规则页面
- 验收：同一订单并发只发一次；断网恢复后补发；库存不超发

### P3 AI + 设置/日志/统计

- 内容：OpenAI 兼容单通道、提示词与开关、事件日志、统计、备份
- 验收：AI 失败自动回退默认回复；页面可完成全部配置

### P4 打包与守护

- 内容：Linux systemd 单元、Windows 托盘与开机自启、安装脚本
- 验收：全新机器按文档一次安装成功

### P5 加固

- 内容：2C2G 压测、7 天浸泡、重连风暴、故障注入
- 验收：10 账号 + 1 msg/s 峰值 < 700MB，无内存泄漏，无重复发货

## 10. 资源预算

| 项 | 预算 |
|---|---|
| 进程基线 | 60–90 MB |
| 每账号 | 3–6 MB |
| 消息与队列缓冲 | < 100 MB |
| 空闲目标 | 150–300 MB |
| 峰值目标 | < 700 MB |
| 浏览器 | 仅按需子进程，单并发，120 秒超时 |

## 11. 部署形态

- Linux：单 systemd 服务，数据目录 `/var/lib/xianyu-lite`，日志轮转
- Windows：托盘进程内启动 HTTP 服务，菜单打开浏览器；数据目录 `%LOCALAPPDATA%\XianyuLite`
- 配置：`XY_` 前缀环境变量 + `.env`

## 12. 安全

- Cookie/Token 使用 AES-GCM 加密存储，密钥来自 `XY_SECRET_KEY`，未配置时生成一次并存入 `setting`
- 单管理员口令使用 PBKDF2-HMAC-SHA256 存储，登录会话使用签名 Cookie
- 默认仅监听 `127.0.0.1`；对外暴露需显式配置并置于反向代理之后

## 13. 风险与对策

| 风险 | 对策 |
|---|---|
| 协议变更/风控 | 协议层隔离 + fixture 回放；触发验证转人工 |
| Cookie/Token 失效 | 刷新失败即转 `error` 并提示重新扫码，不自动爆破 |
| SQLite 单写 | 禁止长事务；批量写入合并；WAL |
| 浏览器资源 | 可选依赖、按需子进程、单并发、硬超时 |
| 旧代码许可 | 不复制实现，仅按接口事实重写 |
| Windows 兼容 | 平台层隔离路径/托盘；不使用 fork 相关能力 |

## 14. 流程约定

- `main` 不在评审前接收实现代码
- 所有实现提交在 `feat/lite-rewrite`；阶段完成并验收后合入 `main`
- 方案变更先改本文件并重新评审
- 每个阶段结束补充协议 fixture 与验收记录
