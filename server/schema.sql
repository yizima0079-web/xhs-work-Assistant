-- datapp Milestone 1 核心表（SQLite 方言；PostgreSQL 迁移时替换引号/自增）
PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS collection_runs (
    id                 TEXT PRIMARY KEY,
    request_id         TEXT,
    platform           TEXT NOT NULL,
    target_type        TEXT NOT NULL,   -- url | keyword | account | post
    target             TEXT NOT NULL,
    max_items          INTEGER NOT NULL DEFAULT 20,
    status             TEXT NOT NULL,   -- queued | running | success | partial | blocked | failed | cancelled | retry_wait
    started_at         TEXT,
    finished_at        TEXT,
    -- 端到端耗时（ms）：仓储层用 started_at/finished_at 算好落库，不在采集侧估算。
    -- 手册 §4.3「必采集的运行指标」里有 latency_ms —— 耗时回归要能直接从库里看出来。
    latency_ms         INTEGER,
    error_code         TEXT,
    error_category     TEXT,            -- network | timeout | auth | rate_limit | argument | empty | parse
    items_found        INTEGER NOT NULL DEFAULT 0,
    items_saved        INTEGER NOT NULL DEFAULT 0,
    retry_count        INTEGER NOT NULL DEFAULT 0,
    rate_limit_signal  INTEGER NOT NULL DEFAULT 0,
    tool_version       TEXT
);

CREATE TABLE IF NOT EXISTS collection_events (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id    TEXT NOT NULL,
    seq       INTEGER NOT NULL DEFAULT 0,
    ts        TEXT NOT NULL,
    level     TEXT NOT NULL,            -- info | warn | error
    category  TEXT,
    code      TEXT,
    message   TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_run ON collection_events(run_id, seq);

CREATE TABLE IF NOT EXISTS contents (
    content_id       TEXT PRIMARY KEY,  -- platform:platform_item_id
    platform         TEXT NOT NULL,
    platform_item_id TEXT NOT NULL,
    content_type     TEXT,              -- note | video | image | mixed
    canonical_url    TEXT,
    author_id        TEXT,
    author_name      TEXT,
    published_at     TEXT,
    collected_at     TEXT NOT NULL,
    title            TEXT,
    text             TEXT,
    cover_url        TEXT,              -- 首图封面（note 详情 SSR）
    cover_local      TEXT,              -- 封面下载到 storage/media 后的 /media/.. 路径（空则退化远程）
    tags             TEXT,              -- JSON array（去 #）
    media            TEXT,              -- JSON array
    engagement       TEXT,              -- JSON object（快照，计数值为 int）
    raw_refs         TEXT,              -- JSON array（原始层引用）
    review_status    TEXT NOT NULL DEFAULT 'pending',
    active_batch_id  TEXT,              -- 生效审核批次（NULL → 回落为 batch_seq 最大的一批）
    -- app 端软隐藏（NULL = 可见）。app 是消费端，web 看板是管理端，共用一个库：
    -- app 上「删除」只打这个标记，数据与派生结果完整保留，web 照常可见可恢复。
    -- 用时间戳而非布尔 —— 「自何时起被隐藏」要能追溯，且 IS NULL 让历史行天然可见。
    app_hidden_at    TEXT,
    UNIQUE(platform, platform_item_id)
);

-- 看板/接口按审核状态过滤与计数（待审 / 已通过 / 已驳回），是最常用的筛选维度。
-- app_hidden_at 也是筛选列，但它是迁移列，索引同样只能放在 _POST_MIGRATION_DDL。
CREATE INDEX IF NOT EXISTS idx_contents_review_status ON contents(review_status);

CREATE TABLE IF NOT EXISTS raw_assets (
    id            TEXT PRIMARY KEY,
    run_id        TEXT,
    content_id    TEXT,
    kind          TEXT,                 -- search | note | comments | feed | user
    mime          TEXT,
    sha256        TEXT,
    bytes         INTEGER NOT NULL DEFAULT 0,
    storage_path  TEXT,
    collected_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_assets_run ON raw_assets(run_id);

CREATE TABLE IF NOT EXISTS claims (
    claim_id TEXT PRIMARY KEY,
    content_id TEXT NOT NULL REFERENCES contents(content_id),
    text TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'unverified',
    confidence REAL,
    claim_type TEXT,           -- fact | opinion | prediction | promotion
    meta TEXT NOT NULL DEFAULT '{}',  -- JSON：entities/time_scope 等抽取附注
    batch_id TEXT NOT NULL DEFAULT '',    -- 审核批次（每次重审新增一批，旧批次保留）
    batch_seq INTEGER NOT NULL DEFAULT 1, -- 批次序号，从 1 递增
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
-- claims 索引：三个查询形态共用一个复合索引 —— 取某内容的批次、按
-- (content_id, batch_id) 取行、以及 MAX(batch_seq)。content_id 是最左前缀，
-- 所以不再单独建 idx_claims_content / idx_claims_batch（老库由 db.py DROP 掉）。
-- 注意 batch_id / batch_seq 是**迁移列**：老库 executescript 阶段还没有这两列，
-- 索引建在 schema.sql 里会让老库启动直接报 "no such column"，
-- 因此实际创建点在 app/db.py 的 _POST_MIGRATION_DDL（补列之后）。

CREATE TABLE IF NOT EXISTS evidence (
    evidence_id TEXT PRIMARY KEY,
    claim_id TEXT NOT NULL REFERENCES claims(claim_id),
    source_kind TEXT NOT NULL,
    source_ref TEXT,
    excerpt TEXT NOT NULL DEFAULT '',
    supports INTEGER,
    strength REAL NOT NULL DEFAULT 1.0,
    collected_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_evidence_claim ON evidence(claim_id);

CREATE TABLE IF NOT EXISTS review_decisions (
    decision_id TEXT PRIMARY KEY,
    claim_id TEXT NOT NULL REFERENCES claims(claim_id),
    status TEXT NOT NULL,
    rationale TEXT NOT NULL DEFAULT '',
    reviewer TEXT NOT NULL DEFAULT 'rule-engine',
    evidence_ids TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_decisions_claim ON review_decisions(claim_id, created_at);

CREATE TABLE IF NOT EXISTS reports (
    report_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    content_ids TEXT NOT NULL DEFAULT '[]',
    payload TEXT NOT NULL DEFAULT '{}',
    markdown TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    schema_version TEXT NOT NULL DEFAULT '1.0'
);

-- 阶段 2：单条作品分析（爆款/平淡归因；JSON+Markdown 双格式，compared_with 为真实基线）
CREATE TABLE IF NOT EXISTS analyses (
    analysis_id    TEXT PRIMARY KEY,
    content_id     TEXT NOT NULL REFERENCES contents(content_id),
    verdict        TEXT NOT NULL DEFAULT 'uncertain',   -- viral | flat | uncertain
    payload        TEXT NOT NULL DEFAULT '{}',
    markdown       TEXT NOT NULL DEFAULT '',
    compared_with  TEXT NOT NULL DEFAULT '[]',          -- JSON array（净化后的真实 content_id）
    focus          TEXT NOT NULL DEFAULT '',            -- 重新分析时用户注入的补充视角（"" = 标准分析）
    created_at     TEXT NOT NULL,
    schema_version TEXT NOT NULL DEFAULT '1.0'
);
CREATE INDEX IF NOT EXISTS idx_analyses_content ON analyses(content_id, created_at);

-- 阶段 3：向量知识库（纯 SQLite 存向量 JSON + 纯 Python 余弦；文档去重靠 content_hash）
-- 两阶段入库：manual 清洗后存 raw_text+markdown、status=pending，向量化后才 ready。
CREATE TABLE IF NOT EXISTS kb_documents (
    doc_id       TEXT PRIMARY KEY,
    source_type  TEXT NOT NULL,              -- analysis | manual | content
    source_id    TEXT,                       -- content_id / analysis_id；manual 置空
    doc_type     TEXT NOT NULL DEFAULT 'general',  -- general（markdown 切片）| qa（Q&A 对，见 kb_qa_pairs）
    title        TEXT NOT NULL,
    author       TEXT,
    tags         TEXT NOT NULL DEFAULT '[]', -- JSON array
    url          TEXT,
    content_hash TEXT NOT NULL,              -- sha256(markdown)，幂等去重
    raw_text     TEXT NOT NULL DEFAULT '',   -- 入库前原始文本（预览/溯源）
    markdown     TEXT NOT NULL DEFAULT '',   -- 清洗后的标题分级 markdown（向量化入口）
    status       TEXT NOT NULL DEFAULT 'pending',  -- pending | embedding | ready | failed
    chunk_count  INTEGER NOT NULL DEFAULT 0,
    created_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_kb_doc_source ON kb_documents(source_type, source_id);
CREATE INDEX IF NOT EXISTS idx_kb_doc_hash ON kb_documents(content_hash);

CREATE TABLE IF NOT EXISTS kb_chunks (
    chunk_id   TEXT PRIMARY KEY,
    doc_id     TEXT NOT NULL REFERENCES kb_documents(doc_id),
    chunk_index INTEGER NOT NULL,
    text       TEXT NOT NULL,
    embedding  TEXT NOT NULL DEFAULT '[]',   -- JSON float array
    modality   TEXT NOT NULL DEFAULT 'text', -- text | image
    image_url  TEXT,
    meta       TEXT NOT NULL DEFAULT '{}',   -- JSON：统一"预设文档形式"
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_kb_chunks_doc ON kb_chunks(doc_id, chunk_index);

-- Q&A 知识条目：doc_type='qa' 的文档下挂的标准「问题 + 答案」对。
-- 知识原子是 pair 而非 markdown 切片；审核（draft→approved）后才向量化，每对生成一个 chunk
-- （qa_id/question 存进 kb_chunks.meta，不加列）。dimensions/evidence/tags 均为 JSON。
CREATE TABLE IF NOT EXISTS kb_qa_pairs (
    qa_id        TEXT PRIMARY KEY,
    doc_id       TEXT NOT NULL REFERENCES kb_documents(doc_id),
    qa_index     INTEGER NOT NULL DEFAULT 0,
    question     TEXT NOT NULL,
    answer       TEXT NOT NULL DEFAULT '',
    dimensions   TEXT NOT NULL DEFAULT '{}', -- JSON：technique/persona/hook/structure/transfer/transfer_risk
    evidence     TEXT NOT NULL DEFAULT '[]', -- JSON array：{source_kind, ref, excerpt}
    tags         TEXT NOT NULL DEFAULT '[]', -- JSON array
    source_type  TEXT NOT NULL DEFAULT 'manual', -- distilled | manual
    source_id    TEXT,                       -- analysis_id / content_id
    source_url   TEXT,
    source_author TEXT,
    status       TEXT NOT NULL DEFAULT 'draft',  -- draft | approved | rejected
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_kb_qa_doc ON kb_qa_pairs(doc_id, qa_index);
CREATE INDEX IF NOT EXISTS idx_kb_qa_status ON kb_qa_pairs(status);

-- app 采集申请：app 端只能「提交意图」（纯写库、零外呼），管理员在 web 看板放行后才真正
-- 建 collection_runs 走 OpenCLI。存在这张表是为了守住「设备令牌可被反编译」这条边界：
-- 令牌泄露最坏后果 = 往待办列表塞垃圾，而不是用你的登录态触发真实平台采集。
-- 放行后 run_id 回指 collection_runs.id；反向的 collection_runs.request_id 列存本表 id。
CREATE TABLE IF NOT EXISTS collection_requests (
    id           TEXT PRIMARY KEY,
    platform     TEXT NOT NULL DEFAULT 'xhs',
    target_type  TEXT NOT NULL DEFAULT 'keyword', -- app 只允许 keyword（检索式采集）
    target       TEXT NOT NULL,
    max_items    INTEGER NOT NULL DEFAULT 10,
    status       TEXT NOT NULL DEFAULT 'pending', -- pending | approved | rejected
    requested_by TEXT NOT NULL DEFAULT 'app',     -- app | web
    run_id       TEXT,                            -- 放行后关联 collection_runs.id
    decided_at   TEXT,
    decided_by   TEXT,
    note         TEXT,                            -- 驳回理由
    created_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_coll_req_status ON collection_requests(status, created_at);
