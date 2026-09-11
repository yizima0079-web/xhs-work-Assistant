"""内容池编排：删除内容（DB 事务 + 提交后的磁盘封面清理）。

删除是不可逆操作，这里只做「按顺序做干净」，不负责二次确认——确认在前端。
"""
from __future__ import annotations

import logging

from app.config import Settings
from app.repositories.base import ContentRepository
from app.services.media import delete_cover

logger = logging.getLogger(__name__)


class ContentService:
    def __init__(self, content_repo: ContentRepository, settings: Settings):
        self.content_repo = content_repo
        self.settings = settings

    async def set_app_hidden(self, content_id: str, hidden: bool) -> bool:
        """切换「app 端可见性」。返回 False = 内容不存在（路由据此 404）。

        只改一个标记位，**不级联**：派生数据（断言/证据/分析/报告/KB 文档）不另打
        标记，它们的可见性在读取时由源 content 的标记**继承**（各仓储的 JOIN/EXISTS
        过滤）。因此恢复是原子的 —— 清掉一个字段，全部派生数据同时回来，不存在
        「内容恢复了但它的知识库文档还是查不到」这种半残中间态。

        与 `delete_content` 是两件事：那个是物理删除（web 的「彻底删除」），
        这个是软隐藏（app 的「删除」）。语义不合并。
        """
        return await self.content_repo.set_app_hidden(content_id, hidden)

    async def delete_content(self, content_id: str) -> dict | None:
        """删除内容及其全部派生数据，返回各表影响面计数。

        内容不存在返回 None（路由据此 404），不把「什么都没删」当成功。
        封面文件在仓储层返回后才 unlink —— 那时事务已提交，不存在回滚后
        「文件已删、行还在」的窗口。删不掉只记 warn，不让孤儿文件把删除变成失败。
        """
        counts = await self.content_repo.delete_content(content_id)
        if counts is None:
            return None
        cover = counts.get("cover_local")
        if delete_cover(cover, self.settings.media_dir):
            counts["cover_deleted"] = 1
        elif cover:
            logger.warning("封面文件删除失败（内容已删除，文件残留）: %s", cover)
        return counts
