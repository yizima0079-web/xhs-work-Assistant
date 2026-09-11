"""领域枚举：任务状态、错误分类、内容类型、目标类型。"""
from __future__ import annotations

from enum import StrEnum


class RunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    RETRY_WAIT = "retry_wait"
    SUCCESS = "success"
    PARTIAL = "partial"
    BLOCKED = "blocked"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ErrorCategory(StrEnum):
    NONE = "none"
    NETWORK = "network"
    TIMEOUT = "timeout"
    AUTH = "auth"
    RATE_LIMIT = "rate_limit"
    ARGUMENT = "argument"
    EMPTY = "empty"
    PARSE = "parse"


class ContentType(StrEnum):
    NOTE = "note"
    VIDEO = "video"
    IMAGE = "image"
    MIXED = "mixed"


class TargetType(StrEnum):
    URL = "url"
    KEYWORD = "keyword"
    ACCOUNT = "account"
    POST = "post"


class ReviewStatus(StrEnum):
    PENDING = "pending"
    EXTRACTED = "extracted"
    APPROVED = "approved"
    REJECTED = "rejected"
    DISPUTED = "disputed"


class ClaimType(StrEnum):
    FACT = "fact"
    OPINION = "opinion"
    PREDICTION = "prediction"
    PROMOTION = "promotion"


class ClaimStatus(StrEnum):
    UNVERIFIED = "unverified"
    SUPPORTED = "supported"
    CONTRADICTED = "contradicted"
    UNCLEAR = "unclear"


class EvidenceKind(StrEnum):
    CONTENT = "content"
    PLATFORM = "platform"
    EXTERNAL = "external"
    HUMAN = "human"


class RequestStatus(StrEnum):
    """app 采集申请状态。approved 才真正建 run；rejected 与 approved 均为终态。"""
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class MediaPolicy(StrEnum):
    METADATA_ONLY = "metadata_only"
    DOWNLOAD_ALLOWED = "download_allowed"
