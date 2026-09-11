# datapp server（Milestone 3：采集 → 审核 → 分析 → Q&A 知识库 + RAG 问答）

社交内容采集后端：OpenCLI 采集适配器 + 限速/熔断/审计 + 三层存储/去重；
Milestone 2 加入 **内容审核（Claim 抽取 → 本地语料交叉检索 → 模型判定）** 与
**跨内容结构化报告（手册 §9.1 JSON + Markdown）**；
Milestone 3 加入 **Q&A 知识蒸馏（作品分析 → 标准「问题+答案」知识条目）** 与
**LangChain RAG 知识库问答（带引用、无据拒答）**。

## 依赖与启动

```powershell
cd F:\datapp\server
uv sync                                  # 安装依赖（开发机推荐，读 pyproject.toml + uv.lock）
uv run uvicorn app.main:app --port 8000  # 启动
```

没有 uv、或拿到的是**不含依赖的分发包**时，按 `requirements.txt` 装（Python 3.14+）：

```powershell
cd F:\datapp\server
py -3.14 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m uvicorn app.main:app --port 8000
```

## 打包与分发约定

**分发包不打包依赖**：包里只放代码（`app/`、`schema.sql`、`requirements.txt`、`requirements-dev.txt`、`.env.example`）
和运行时数据，依赖由使用者在目标机器上按 `requirements.txt` 自行安装。原因：Python 依赖按平台/架构编译
（uvloop 在 Windows 本身就装不上），把 `.venv` 打进包等于把「本机环境」当成「通用环境」，换机器就崩，
体积还会从几 MB 涨到上百 MB。

打包时必须排除（`.gitignore` 已覆盖）：
`server/.venv/`、`server/.pytest_cache/`、`__pycache__/`、`client/node_modules/`、`client/dist/`、
`vendor/`、`app/build/`、`storage/`、`.tmp/`，以及 **`server/.env`（含密钥，绝不进包）**。

依赖版本以 `pyproject.toml` 为真源；`requirements.txt` 是「不带依赖的分发包」用的安装清单（锁死版本），
重新生成：`uv export --format requirements-txt --no-hashes --no-dev -o requirements.txt`。

### 依赖说明（2026-09-11 精简过一轮）

- 主依赖只留有真实调用方的包。`uvicorn` **不带 `[standard]`**：项目没有 WebSocket / HTTP2 / uvloop 需求，
  热重载用自带的 statreload 就够，省掉 websockets、uvloop（Windows 本来也装不上）等一批包。
- `python-multipart` 虽然没有直接 `import`，但 `/kb/upload` 的 `UploadFile` 由 Starlette 在运行时要求它，**不能删**。
- `langchain-core` 只用在 `app/services/lc.py` 一处，且带纯 Python 回退实现；装不上也不会挂，`/health` 的 `LC_BACKEND` 会显示实际后端。
- **MongoDB 是可选 extra，不是主依赖**：后端尚未实现（`repositories/` 只有 SQLite），目前只有 `tools/mongodb_check.py` 用 pymongo。
  需要时：`uv sync --extra mongodb`。

服务端地址：`http://127.0.0.1:8000`；客户端 Vite 地址：`http://127.0.0.1:5173`。
客户端 `/api` 请求通过 Vite 代理转发到服务端 8000 端口。

管理员登录：账号 `admin`；密码通过 `DATAPP_ADMIN_PASSWORD_HASH` 配置。登录后服务端通过 HttpOnly Cookie 放行业务 API，错误凭据返回 401，无法进入看板。

前置：OpenCLI 已构建（`vendor/OpenCLI-1.8.8/dist/src/main.js`），Edge/Chrome 已登录小红书且 Browser Bridge（`127.0.0.1:19825`）在线。

> **`vendor/` 不入版本控制**（见根 `.gitignore`）—— OpenCLI 是外部项目（`@jackwener/opencli`），
> 2596 个文件进来只会淹没本仓库的真实改动。克隆仓库后需**自行构建**到
> `vendor/OpenCLI-1.8.8/dist/`，或把 `DATAPP_OPENCLI_MAIN` 指向已有的产物。
> 没有它后端照常启动，只是采集功能不可用 —— `/api/v1/health` 的 adapter 字段会报不健康，
> 那是**诚实状态**（确实没装采集工具），不是故障。

## 配置

复制 `.env.example` 为 `.env` 或设环境变量（前缀 `DATAPP_`）。关键项：

- `DATAPP_DB_PATH`：SQLite 库（默认 `F:\datapp\storage\datapp.db`）
- `DATAPP_RAW_DIR`：原始响应落盘目录
- `OPENCLI_PROFILE`：Browser Bridge contextId（如 `3jpz9ujj`）
- `DATAPP_RATE_PER_SECOND`：令牌桶速率（默认 0.1 = 10s/次，另加 0–40% 随机抖动，落在手册要求的 5–30s 区间）
- `DATAPP_CIRCUIT_BREAKER_THRESHOLD`：连续硬失败熔断阈值（默认 3）

### 模型层（Milestone 2，可选）

审核抽取/判定与报告生成调用 OpenAI 兼容 `/chat/completions`（默认 DashScope 兼容端点）。
**key 只写进 `server/.env`，不进代码/日志/测试**；未配置时审核/报告接口返回 503，绝不以假数据兜底。

- `DATAPP_LLM_BASE_URL`：兼容端点（默认 `https://dashscope.aliyuncs.com/compatible-mode/v1`）
- `DATAPP_LLM_API_KEY`：API key（留空 = 未配置 → 审核/报告/Q&A 蒸馏/问答 503）
- `DATAPP_LLM_MODEL`：模型名（如 `qwen3.8-flash`）
- `DATAPP_LLM_TIMEOUT`：单次请求超时秒数。默认 90；`qwen3.8-flash` 先推理再作答，
  **Q&A 蒸馏 prompt 较长（作品数据 + 基线 + 归因报告 + 六维拆解 schema）实测需 >120s**，
  建议 `.env` 里设 300–420，否则蒸馏会 ReadTimeout → 503。

### Q&A 知识 + RAG 问答（Milestone 3）

- `DATAPP_QA_MAX_PAIRS`：单次蒸馏最多产出问答对数（默认 8）
- `DATAPP_QA_BOOST`：Q&A chunk 同分优先权重，`0.0` = 关闭（**默认关闭时排序与旧行为逐字节一致**）
- `DATAPP_RAG_TOP_K`：问答检索条数（默认 6）
- `DATAPP_RAG_MIN_SCORE`：拒答阈值，最高余弦低于此值 → 无据拒答且**不调用模型**（默认 0.25）
- `DATAPP_RAG_MAX_CONTEXT_CHARS`：拼进 prompt 的检索上下文上限（默认 6000）

LangChain 仅作**编排层**：`langchain_core` 的 import 只允许出现在 `app/services/lc.py`，
其余模块从该触点引入。chunking / DashScope embedding / SQLite 向量 / Python 余弦检索全部沿用既有实现，零数据迁移。
`langchain-core` 装不上时 `lc.py` 自动退化为等价 shim（`/health` 的 `lc` 字段会显示 `fallback`）。
`lc.py` **强制**把 `LANGCHAIN_TRACING_V2` / `LANGSMITH_TRACING` / `LANGCHAIN_TRACING` 写死为 `false`，
防止 prompt 与采集内容外发（`/health` 回显 `lc_tracing: false`）。

### 管理员门禁

对外暴露（局域网 / 公网）前**必改**：`DATAPP_AUTH_SECRET` 与 `DATAPP_ADMIN_PASSWORD_HASH`
的默认值是**公开常量**，不换等于门禁形同虚设。详见 `.env.example` 的 `[公网必改]` 标注。

### 存活探针用 `/health`，不要用 `/api/v1/health`

**`/api/v1/health` 会 spawn 一个 OpenCLI 子进程做真实登录态探测**，冷缓存实测 8.9s、偶发超过 30s；
没装采集工具时还直接 500（**诚实状态**，不是故障）。拿它做存活/健康检查，会把「服务好得很、
只是探针慢」误判成「服务挂了」，平台会反复重启。

探针一律用根路径 **`/health`**（纯内存，实测 3.8ms）。

## API

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/v1/health` | 适配器登录态 + 熔断状态；结果带 TTL 缓存（30s），响应含 `checked_at`（真实探测时间）与 `cached`（本次是否命中缓存）——陈旧状态对调用方可见，不伪装实时 |
| POST | `/api/v1/collection-runs` | 创建采集任务（异步，返回 `id`） |
| GET | `/api/v1/collection-runs` | 任务列表 |
| GET | `/api/v1/collection-runs/{id}` | 任务详情 + 事件时间线 |
| POST | `/api/v1/collection-runs/{id}/cancel` | 取消任务 |
| GET | `/api/v1/contents` | 标准化内容列表 |
| GET | `/api/v1/contents/summary` | **内容池概览**：一次返回每条内容的派生计数（`analysis_count` / `latest_analysis_id` / `report_count` / `claim_count` / `review_batch_count` / `kb_doc_count`），替代前端逐条探测的 N+1。**路由必须声明在 `/contents/{id}` 之前** |
| POST | `/api/v1/contents/{content_id}/analyze` | 作品分析；可选 body `{focus}`（≤500 字）注入**补充视角** —— 只补充观察侧重，不改判定口径；每次调用新增一条分析记录（旧版保留可回看）|
| GET | `/api/v1/contents/{content_id}/analyses` | 分析历史，倒序（新→旧）；每条含 `in_kb` 标记该版本是否已入库 |
| GET | `/api/v1/contents/{id}` | 内容详情 |
| DELETE | `/api/v1/contents/{id}` | **删除内容及其全部下游**（204）。单事务按 FK 逆序清理 evidence/decisions/claims/analyses/kb_chunks/kb_qa_pairs/kb_documents/raw_assets/contents；含该 id 的报告**摘除该 id 而非删整篇**（摘空才删）；封面文件在提交后 unlink，失败只记 warn 不阻断。未知 id → 404 |
| POST | `/api/v1/contents/{content_id}/review` | 触发自动审核（Claim 抽取→检索→判定）；`?force=true` 为**重新审核** → **新增一个审核批次**，旧批次完整保留（不再覆盖）。无 `force` 且已审核仍 409 |
| GET | `/api/v1/contents/{content_id}/claims` | 内容审核详情（claims + evidence + decisions）；可选 `?batch_id=` 指定版本，缺省取**生效批次** |
| GET | `/api/v1/contents/{content_id}/review-batches` | 审核批次列表，倒序；`[{batch_id, batch_seq, created_at, claim_count, status, active}]` |
| POST | `/api/v1/contents/{content_id}/review-batches/{batch_id}/activate` | 切换生效批次；`contents.review_status` 由该批次重算。批次不属于该内容 → `KeyError` |
| GET | `/api/v1/claims/{claim_id}` | 单条断言快照 |
| POST | `/api/v1/claims/{claim_id}/decision` | 人工复核改判（reviewer=human）|
| POST | `/api/v1/reports` | 跨内容生成 §9.1 结构化报告 |
| GET | `/api/v1/reports` | 报告列表；`?content_id=` 只返回包含该内容的报告（单篇报告的「查看历史」）|
| GET | `/api/v1/reports/{report_id}` | 报告详情 |
| GET | `/api/v1/kb/documents` | 知识库文档列表（含 `doc_type` / `qa_pair_count`）|
| POST | `/api/v1/kb/documents` | 手动文档清洗存 pending |
| POST | `/api/v1/kb/documents/{id}/vectorize` | 单文档向量化（`doc_type=qa` 只嵌 approved 问答对）|
| POST | `/api/v1/kb/documents/vectorize` | 批量向量化（逐条回执，单条失败不阻断）|
| DELETE | `/api/v1/kb/documents/{id}` | 删除文档及其 chunk / 问答对 |
| POST | `/api/v1/kb/search` | 语义检索（可选 `qa_boost`）|
| POST | `/api/v1/kb/qa/documents` | **手动录入问答**（结构化终态，**不调用模型**，无 key 也可用）|
| POST | `/api/v1/kb/qa/distill` | 人工粘贴文本 → 清洗 → 蒸馏问答草稿 |
| POST | `/api/v1/kb/analyses/{id}/distill-qa` | 作品分析 → 问答草稿（`?force=true` 重蒸馏）|
| POST | `/api/v1/kb/contents/{id}/distill-qa` | 作品（含最近分析）→ 问答草稿 |
| GET | `/api/v1/kb/qa/documents/{doc_id}/pairs` | 文档下全部问答对 |
| PATCH | `/api/v1/kb/qa/pairs/{qa_id}` | 编辑问答对（approved 被改写自动回落 draft）|
| POST | `/api/v1/kb/qa/pairs/approve` \| `/reject` | `{qa_ids}` → `{changed, requested}` |
| DELETE | `/api/v1/kb/qa/pairs/{qa_id}` | 删除单条问答对 |
| POST | `/api/v1/kb/ask` | **RAG 问答**：带引用作答，无据拒答 |

### Q&A 知识语义（Milestone 3）

知识原子从 markdown 切片改为标准「问题 + 答案」。一条 = `question` + `answer` +
六维拆解（`technique` 表现手法 / `persona` IP 人设 / `hook` 开头钩子 / `structure` 结构节奏 /
`transfer` 迁移建议 / `transfer_risk` 失败风险）+ `evidence` + 溯源，回答「为什么这个内容结合这种演绎能爆」
「换别的方式能不能爆」。

- **双通道**：`AI 蒸馏`（作品分析 → 草稿，走人工审核）与 `手动录入`（人写即终态）。
  手动通道**不经过 LLM 清洗**，因此无 key 也能用；红线由 `draft → approved` 的人工闸门满足。
- **两阶段沿用**：`蒸馏/录入 → doc(pending) + pairs(draft) → 人工 approve → 向量化 → ready`。
  未审核内容**永不进入 `kb_chunks`**，不参与检索。
- **每对一 chunk**：Q&A 路径刻意绕过按 500 字符切分的 `chunk_text`，问题与答案同块不拆散
  （整块封顶 1500 字符，只截尾部的迁移/风险段）。
- **幂等**：同 `(source_type, source_id)` 已有 Q&A 文档 → 直接复用；`force=true` 重蒸馏
  **只删草稿**，`approved` 永不自动删除。
- **防幻觉**：`sanitize_qa_payload` 用服务端登记的来源表给 `evidence.ref` 求交，越界引用丢弃并计数；
  未知维度收进 `extra`；0 对存活即抛错，绝不落空文档进库。

### RAG 拒答三闸（`POST /kb/ask`）

1. **检索为空** → 直接拒答，**不调用模型**（`reason=empty_retrieval`）；
2. **`top_score < DATAPP_RAG_MIN_SCORE`** → 拒答并回传 `top_score` / `retrieval_count` 供校准
   （`reason=below_threshold`，同样不调用模型）；
3. **有效引用为 0** → 模型 `citations` 与本次检索命中集求交为空时**强制转拒答，原始答案文本丢弃不展示**
   （`reason=no_valid_citation`）；引用 `quote` 经空白归一做子串校验，对齐则 `verified=true`。

链路 = `RunnableLambda(_prompt_step) | RunnableLambda(_model_step) | RunnableLambda(_guard_step)`（LCEL），
真 `langchain-core` 与内置 shim 两条后端跑的是**同一份链代码**，行为等价由 `tests/test_lc_shim.py` 锁定。

### 审核语义

- 内容终态状态机：`supported+达标 → approved`；`contradicted+supported 并存 → disputed`；
  全 `contradicted / 无核` → `rejected`；含 `unverified/unclear` 或置信度 < 0.6 → `extracted`（待人工）。
- 只处理 `pending` 内容；重复自动审核 → 409。
- **防幻觉**：模型判 `supported/contradicted` 必须有至少一条来自本地已采集语料库的真实证据；
  引用了候选集之外的 content_id 一律丢弃，无证据的结论降级 `unclear`。模型输出 ≠ 事实，报告保留可审计 JSON + Markdown 双格式。

### 知识库红线：未审核内容不得进 `kb_chunks`

两层防线，互不依赖：

| 层 | 位置 | 行为 |
|---|---|---|
| **C1 入库门禁** | `services/knowledge_base.py::vectorize_content` / `vectorize_analysis` | 源内容 `review_status != approved` → 拒绝，HTTP **409** |
| **C2 检索过滤** | `repositories/kb.py::iter_retrievable_chunks` | SQL JOIN 过滤，越线 chunk **物理上命中不到** |

C1 是**状态判断**而非永久拉黑：内容补审到 `approved` 后即可正常入库（`ReviewGateError` 继承 `ValueError`，
路由层在 `ValueError→422` 之前单独捕获并映射 409 —— 状态冲突，不是校验失败）。
analysis 型文档的可信度依附于源内容，源内容未过则一并拦。

C2 是深防线：即便数据里混进脏 chunk，检索也命中不到、更不会被引用作答。
`iter_all_chunks` 保持原语义不动（供管理/统计）。干净数据下 JOIN 过滤零行，
结果集与 `iter_all_chunks` 完全一致（同 `rowid` 排序），**排序不变量不受影响**。

> **副作用要知情**：若库中没有任何 `approved` 内容，`POST /kb/search` 返回 `[]`、
> `POST /kb/ask` 返回 `{"answered": false, "reason": "empty_retrieval"}`。
> 这是门禁在正常工作，不是故障 —— 发布云端副本前先确认有已审内容。

体检：`uv run python tools/verify_schema.py` 会同时校验数据层（越线文档数）与行为层（越线 chunk 可检索命中数）。
存量越线登记在 `tools/redline_baseline.json` 转 WARN；**新增**越线仍 FAIL。基线文件缺失 → 全部 FAIL（安全默认）。

创建任务示例：

```powershell
curl -X POST http://127.0.0.1:8000/api/v1/collection-runs `
  -H "Content-Type: application/json" `
  -d '{"target_type":"keyword","target":"AI眼镜","max_items":2}'
```

## 测试

```powershell
uv run pytest    # 全部离线快照测试，不触真实平台
```

表结构与数据不变量的体检（逐表逐列对比 `schema.sql`、外键孤儿、枚举合法性、红线检查）：

```powershell
uv run python -m tools.verify_schema
```

## 真实 smoke（可选，极小样本）

需 Edge + Browser Bridge 在线：

```powershell
$env:OPENCLI_PROFILE="3jpz9ujj"
curl -X POST http://127.0.0.1:8000/api/v1/collection-runs `
  -H "Content-Type: application/json" `
  -d '{"target_type":"keyword","target":"AI眼镜","max_items":1}'
```

命中验证码 / 300017 / 300031 / 403 立即停止（见 `agent.md` 红线）。
