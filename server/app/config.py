"""集中配置：读环境变量（前缀 DATAPP_），默认值指向项目内 storage/ 与 vendor/。"""
from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# config.py 位于 server/app/config.py -> parents[2] = 项目根 F:\datapp
PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="DATAPP_", env_file=".env", extra="ignore")

    # 存储
    db_backend: str = "sqlite"  # sqlite | mongodb；默认保持本地开发行为
    db_path: Path = PROJECT_ROOT / "storage" / "datapp.db"
    mongodb_uri: str | None = None
    mongodb_database: str = "datapp"
    mongodb_server_selection_timeout_ms: int = 5000
    raw_dir: Path = PROJECT_ROOT / "storage" / "raw"
    media_dir: Path = PROJECT_ROOT / "storage" / "media"  # 本地封面图（从远程 xhs 下载落盘）

    # OpenCLI 子进程
    opencli_main: Path = (
        PROJECT_ROOT / "vendor" / "OpenCLI-1.8.8" / "dist" / "src" / "main.js"
    )
    opencli_node: str = "node"
    opencli_profile: str | None = None  # 显式指定 contextId；缺省走 daemon 默认
    opencli_command_timeout: int = 60
    # Bridge 连接预算：扩展/daemon 不在线时让 OpenCLI 快速认输，不拖满命令超时
    opencli_connect_timeout: int = 45
    # 硬超时 = 命令超时 + grace：越过就用 kill 收掉子进程（防 node 挂着占浏览器会话）
    opencli_timeout_grace: int = 30
    # 工具版本写进 Run.tool_version，用于按版本复现采集差异
    opencli_version: str = "1.8.8"

    # 限速 / 熔断
    # 0.1 = 10s/条，落在 AGENTS.md 的 5–30s 区间；原 0.5（2s）低于手册下限
    rate_per_second: float = 0.1
    rate_capacity: int = 1
    # 间隔随机放大 [1, 1+rate_jitter]，只增不减：合规上更保守，也不会短于配置间隔
    rate_jitter: float = 0.4
    circuit_breaker_threshold: int = 3
    global_concurrency: int = 1

    # 任务
    max_items_default: int = 20
    # app 采集申请的单次条数上限。服务端强制钳制，不信任客户端 ——
    # 对齐 AGENTS.md「单任务默认最多 10 条」。web 管理员直建任务走 max_items_default，
    # 两条口子分开设，是为了让「可被反编译的令牌」这条路的上限独立可控。
    collection_request_max_items: int = 10

    # 健康检查 TTL：/health 每次都 spawn OpenCLI 子进程，看板轮询下必须缓存
    health_cache_ttl: float = 30.0

    # 模型层（OpenAI-compatible，默认 DashScope 兼容端点；key 只进 server/.env）
    llm_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    llm_api_key: str | None = None
    llm_model: str = "qwen3.8-flash"
    # 该模型先走 reasoning 再回答，深 prompt 首 token 可能超 30s；120 内稳定。
    llm_timeout: float = 90.0

    # 向量模型：tongyi-embedding-vision-flash 走 DashScope 原生多模态端点（非 compatible-mode），
    # 模型名/维度独立于 chat；key 复用 llm_api_key。见 docs 阶段 3。
    embedding_model: str = "tongyi-embedding-vision-flash"
    embedding_base_url: str = "https://dashscope.aliyuncs.com"
    embedding_dimension: int = 768
    embedding_timeout: float = 30.0

    # Q&A 知识蒸馏 + RAG 问答（编排层见 app/services/lc.py）
    qa_max_pairs: int = 8          # 单次蒸馏最多产出的问答对数
    qa_boost: float = 0.0          # Q&A chunk 同分优先权重；0.0 = 关闭（排序与旧行为逐字节一致）
    rag_top_k: int = 6             # 问答检索条数
    rag_min_score: float = 0.25    # 拒答阈值：最高余弦低于此值 → 无据拒答，不调用模型
    rag_max_context_chars: int = 6000  # 拼进 prompt 的检索上下文上限

    # 管理员门禁：密码仅保存为 PBKDF2 哈希；不要把明文密码写入代码、日志或报告。
    # 注意：admin_password_hash / auth_secret 的默认值是**本地开发用**的已知常量，
    # 对外暴露（局域网 / 公网）前必须在环境变量里换掉，否则门禁形同虚设。
    admin_username: str = "admin"
    admin_password_hash: str = "0567ddb7276a77a4d3b43e7800c4cd87deaeb52a90574425c6fcb8243c10ef28"
    auth_secret: str = "datapp-local-session-secret-change-me"
    auth_session_hours: int = 12

    # 移动端设备令牌：app 用 X-Datapp-App-Token 头访问，**免账号密码启动**，可读可写，
    # 但**不含采集模块**（见 auth.py::_APP_TOKEN_DENY_PREFIXES）。
    # 注意：令牌会被编进 APK（经 local.properties → BuildConfig），反编译可取 ——
    # 这是「app 不输密码」的固有代价，排除采集模块把后果限制在「改库里的数据」。
    # 未设置 → 该通道关闭，app 回落登录页。
    app_token: str | None = None

    @property
    def opencli_cwd(self) -> Path:
        """OpenCLI 项目目录（node 的相对路径/配置基线）。"""
        return self.opencli_main.parents[2]


settings = Settings()
