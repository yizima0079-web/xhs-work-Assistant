# datapp

社交内容采集 → 多轮信息源审核 → 可信分析 → RAG 知识库 + Agent 问答的数据管道项目。

## 目录结构

- `client/` — 前端看板（React + TypeScript + Vite）
- `server/` — 后端（Python + FastAPI）：采集 / 审核 / 分析 / RAG / Agent
- `app/` — Android 应用骨架（Gradle）
- `vendor/OpenCLI-1.8.8/` — 采集工具（`@jackwener/opencli`，登录态浏览器采集）
- `docs/` — 需求、架构手册、采集验证报告、[app 端对接契约](docs/app端对接契约.md)

## 当前状态（2026-09-11）

- **Milestone 0 采集可行性已验证**：OpenCLI v1.8.8 已构建，`doctor` 全绿，whoami/feed/note/comments/search 实测可用。证据见 [docs/opencil采集可行性验证报告.md](docs/opencil采集可行性验证报告.md) §11–§13。
- **Milestone 1 本地数据闭环已落地**：`server/` FastAPI 后端 + `CollectorAdapter` + 限速/熔断/审计 + SQLite Repository；`client/` React 看板。
- **Milestone 2 审核与分析已落地**：Claim/Evidence/ReviewDecision、多源检索、结构化报告；审核已**批次版本化**（重审新增批次、旧批次保留、可切换生效版本）。
- **Milestone 3 Q&A 知识蒸馏 + RAG 问答已落地**：六维拆解知识原子、向量化、检索加权、带引用作答 + 无据拒答。
- 真库结构体检通过（2026-09-10），工具 `server/tools/verify_schema.py`，契约见 [docs/app端对接契约.md](docs/app端对接契约.md)。
- **app 端数据双通已打通**（2026-09-10）：baseUrl 经 `BuildConfig` 注入（源头 `gradle.properties`
  的 `datapp.baseUrl`；**2026-09-11 起它只当首次启动的中性兜底**，实际地址在 app 内设置并持久化，
  见下面那条）、去掉 `TempData` 静默回退。模拟器 `emulator-5554` 实测：
  真实数据 + 封面图 200。详见 [docs/开发日志.md](docs/开发日志.md) 与
  [docs/app端对接契约.md](docs/app端对接契约.md) §1.3。
  （当日认证走的只读 Token 通道已于 2026-09-11 整条删除。）
- **app 端全功能继承已落地**（2026-09-11）：底部四 tab **首页 / 内容 / 知识库 / 我的**；
  内容 tab 内嵌**分析 / 审核 / 报告**三面板，知识库 tab 承载**语义检索 / 问答 / 文档 / 向量化 /
  问答对审核**。`TempData.kt` 已整份删除。模拟器上**逐个点过全部写操作**
  —— 放行的 200/204、被红线拒绝的 409、服务端模型层 503 都走了一遍，服务端日志无 401/403
  （来源 IP + 状态码见 [docs/开发日志.md](docs/开发日志.md) §四）。
- **app 端免账号密码登录已落地**（2026-09-11）：移动端设备令牌
  （`X-Datapp-App-Token`，权限 = 管理员**减去采集模块**）。令牌经 `local.properties` →
  `buildConfigField` 注入 APK，app 启动**零输入**直进主界面；Web 看板看数据走管理员 Cookie。
  模拟器实测：冷启动无登录页、全请求 200、写操作（断言人工改判）**200**、app 来源 IP
  **零 401**；清空令牌重编确认**回落登录页**兜底有效。契约见
  [docs/app端对接契约.md](docs/app端对接契约.md) §1.3.1–§1.3.3。
- **app 端「删除」= 软隐藏已落地**（2026-09-11）：app 详情页顶部的「删除」只写
  `contents.app_hidden_at` 时间戳，**不是真删** —— 内容与它的断言/证据/分析/报告/KB 文档/QA 对
  全部原样保留，web 看板照常可见并带「app 端已隐藏」chip，一键「重新同步到 app 端」即原子恢复。
  app 通道（设备令牌）看不到隐藏内容及其**全部派生数据**（含检索命中与问答引用），
  详情返回 **404 而非 403**（403 会变成存在性预言机）；恢复仅管理员 Cookie 可调，设备令牌打
  `DELETE .../app-hidden` 是 401。既有硬删 `DELETE /contents/{id}` 一行未改，留给 web 的「彻底删除」。
  新增 `tests/test_app_hidden.py` 36 例（**全量 434 passed**，零回归），模拟器 + 真库端到端实测通过。
  细节见 [docs/开发日志.md](docs/开发日志.md) 与 [docs/app端对接契约.md](docs/app端对接契约.md) §3.4。
- **app 后端地址改成运行时切换（2026-09-11）**：起因为一次故障 —— 本机 DHCP 换了整个网段
  （`192.168.10.145` → `192.168.0.109`），APK 里编译进去的地址指向已不存在的主机，okhttp 拿到的是
  「连接被静默丢弃」而非明确拒绝，报错是极具误导性的 `unexpected end of stream`；后端其实一直
  `/health` 200。现在地址在 **app 内改**：「我的」→ 连接 →「修改地址」，登录页也有入口
  （**必须两处都有** —— Cookie 模式下地址填错会停在登录页，而「我的」页要会话就绪才可达，
  只有一处会把用户锁死）。存本机、重启生效，支持「测试连接」与「自动探测」（候选列表并发探
  `/health`，**不做网段扫描**，DHCP 换网段时仍需手填）。编译期默认改为中性值
  `http://127.0.0.1:8000`，不再需要改 `gradle.properties` 重编。**探针必须用根路径 `/health`**，
  别改成 `/api/v1/health`（会 spawn OpenCLI 探测登录态，冷缓存 8.9s／偶发 >30s／没装工具时 500，
  会把「探针慢」误判成「地址不通」）。实测四项全过（改地址生效／持久化／自动探测／登录页不死锁），
  单测 16 绿。细节与踩坑见 [docs/开发日志.md](docs/开发日志.md)、契约 §1.1。
- **采集不继承到 app**：OpenCLI 只做已登录浏览器会话采集，账号态在桌面端 —— **设计边界，非缺陷**。
  设备令牌进一步在服务端把 `/collection-runs`、`/browser/*` 对它关死（前缀匹配，覆盖
  `/{run_id}/cancel` 这类动态段），双重确认这条边界。
- **已修（2026-09-11）**：KB 路由的 `_status()` 漏判 `ModelProviderError`，把 LLM 上游
  超时/断连误报成 503「未配置」——这正是上一轮把 `distill-qa` 的 503 误判成「缺 key」
  的根因。修复 + 2 例回归测试（已验证还原旧实现即变红），见 [docs/开发日志.md](docs/开发日志.md)。
- **表结构优化已完成（2026-09-11）**：新增 `collection_runs.latency_ms`（仓储层算，落库，客户端任务详情展示）；
  claims 的两个窄索引收敛为 `idx_claims_content_batch(content_id, batch_id, batch_seq)`；新增 `idx_contents_review_status`；
  `tools/verify_schema.py` 补上漏登记的 `collection_requests`（12 → 13 张表）。取舍与索引清单见
  [docs/技术手册.md](docs/技术手册.md) §8.1，过程见 [docs/开发日志.md](docs/开发日志.md)。
- **依赖精简已完成（2026-09-11）**：`pymongo` 移出主依赖（改 `--extra mongodb`）、`uvicorn[standard]` 降为 `uvicorn`；
  app 侧删掉零引用的 `lifecycle-runtime-ktx`、`ui-tooling-preview`、Compose 测试与 Espresso 依赖。改过 `pyproject.toml`，
  本地要跑一次 `uv lock`（或 `uv sync`）再提交。
- **分发清单已就位（2026-09-11）**：`server/requirements.txt`（运行时，版本锁死）
  与 `server/requirements-dev.txt`（开发/测试）。**分发包不打包依赖**：`server/.venv` / `node_modules` /
  `vendor/` / `app/build` 都不进包，使用者按 `requirements.txt` 自装 —— 约定见
  [server/README.md](server/README.md)「打包与分发约定」。
- **云端只读副本路线已废弃并清理完毕（2026-09-11）**：Docker 容器化 + 微信云托管整条路线砍掉
  —— 采集依赖本机登录态浏览器，副本只能读、功能残缺，运维成本远大于收益。现行形态是
  **本地后端 + 局域网直连**。连带删除：`server/deploy/`（Dockerfile / publish.sh / verify_image.sh）、
  根 `.dockerignore`、`docs/云托管部署手册.md`；`DATAPP_READONLY` 只读模式与 `X-Datapp-Token`
  只读 Token 通道从代码、测试、配置、文档中**整条移除**。**认证收敛回两条**：设备令牌（app）
  \+ 管理员 Cookie（web）。`vendor/OpenCLI-1.8.8/` 不入库（已加 `.gitignore`），需自行构建。
  过程见 [docs/开发日志.md](docs/开发日志.md)。
- **下一步**：① 真机（非模拟器）实测；② `distill-qa` 实测耗时 120.7s 触顶 LLM 读超时
  （同进程 `/kb/ask` 返回 200 且带 citation，key 与上游均正常）——待定是抬高
  `DATAPP_LLM_TIMEOUT` 还是下调 `DATAPP_QA_MAX_PAIRS`（默认 8）缩短单次蒸馏；
  ③ **app 隐藏功能的遗留项**（均为存量问题，非本次引入）：`/media/*` 静态挂载免认证，
  隐藏内容的封面 URL 已知仍可取；`analyses.payload` / 历史 `evidence.source_ref` /
  历史 `report.payload` 里可能残留隐藏内容的 **id 字符串**（不含标题/正文，防住了「新增」
  没清洗「存量」）；**app 目前仍能真删内容**（`DELETE /api/v1/contents/{id}` 被
  `tests/test_app_token.py` 锁定，收回需同时改 `auth.py` 与既有测试）。

OpenCLI 只负责已登录浏览器会话采集；数据库、审核、分析、报告和 RAG 属于 datapp 外层系统。

## 关键约束（完整版见 agent.md）

- 只采集公开或用户授权数据；**不绕过风控 / 验证码 / 登录**
- **app 设备令牌不含采集模块**：`/collection-runs`、`/browser/*` 对它一律 401。令牌编在
  APK 里可被反编译 —— 排除采集把泄露后果限制在「改库里的数据」，隔开「触发真实平台采集」
- 采集低频少量，全局并发 1，外层令牌桶 + 熔断 + 审计
- 模型输出 ≠ 事实；报告 JSON + Markdown 双格式可审计；**不伪造成功**
- **未审核内容永不进 `kb_chunks`**；citations 无有效引用 → 强制拒答并丢弃模型文本
  - 门禁已双层闭环：入库侧 `vectorize_content()` 校验 `review_status='approved'`，检索侧
    `iter_retrievable_chunks()` 用 SQL JOIN 二次过滤（行为层红线，脏 chunk 物理命中不到）。
    体检数据层红线项 **0 违反**；此前记录的越线数据已不在库中。详见 [docs/开发日志.md](docs/开发日志.md) §十。

## 工作约定

- 中文交流，简练直接
- 架构依据 [docs/技术手册.md](docs/技术手册.md)（v1.0）
- 边界红线依据 [AGENTS.md](AGENTS.md)；兼容旧路径 [agent.md](agent.md)
