"""MongoDB Atlas 连接检查；只输出脱敏地址、数据库名和 ping 结果。"""
from __future__ import annotations

import asyncio
import os
import re
import sys

from pymongo import AsyncMongoClient


def _masked_uri(uri: str) -> str:
    return re.sub(r"(mongodb(?:\+srv)?://[^:/?#]+:)[^@]+(@)", r"\1***\2", uri)


async def main() -> int:
    uri = os.getenv("DATAPP_MONGODB_URI", "").strip()
    database = os.getenv("DATAPP_MONGODB_DATABASE", "datapp").strip() or "datapp"
    if not uri:
        print("未配置 DATAPP_MONGODB_URI；请从 Atlas Connect -> Drivers 获取连接串后写入 server/.env。")
        return 2

    client = AsyncMongoClient(
        uri,
        serverSelectionTimeoutMS=int(os.getenv("DATAPP_MONGODB_SERVER_SELECTION_TIMEOUT_MS", "5000")),
    )
    try:
        result = await client.admin.command("ping")
        await client[database].command("ping")
        print(f"MongoDB Atlas 连接成功: {_masked_uri(uri)}")
        print(f"目标数据库: {database}; ping: {result.get('ok')}")
        return 0
    except Exception as exc:  # 连接检查需要把 Atlas 分类错误交给操作者处理
        print(f"MongoDB Atlas 连接失败: {type(exc).__name__}: {str(exc)[:300]}")
        return 1
    finally:
        await client.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
