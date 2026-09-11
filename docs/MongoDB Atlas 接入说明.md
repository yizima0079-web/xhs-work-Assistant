# MongoDB Atlas 接入说明

## 当前状态

- 已生成 MongoDB Atlas 建库、校验器和索引脚本：`server/mongodb_schema.js`
- 已增加配置模板：`server/.env.example`
- 已增加脱敏连接检查：`server/tools/mongodb_check.py`
- `pymongo` 已移出主依赖，改为可选 extra：`uv sync --extra mongodb`（MongoDB 后端仍未实现，主依赖里放它属于冗余）
- 当前业务 Repository 仍由 `server/app/main.py` 使用 SQLite 实现，尚未切换生产读写

## 需要提供或执行的操作

1. 在 MongoDB Atlas 创建 Cluster。
2. 创建 Database User，提供给服务端使用的用户名和密码。
3. 在 Network Access 中加入运行服务的公网 IP；不要直接放开 `0.0.0.0/0`。
4. 在 Atlas 的 Connect → Drivers 选择 Python，复制连接串。
5. 复制模板并填写本地环境文件：

```powershell
Copy-Item server/.env.example server/.env
```

只填写 `server/.env`，不要把连接串贴到聊天、代码、日志或提交记录中。

## 连接检查

```powershell
uv --cache-dir F:\datapp\.uv-cache run --extra mongodb python -m tools.mongodb_check
```

成功后再初始化 Atlas 结构：

```powershell
mongosh "$env:DATAPP_MONGODB_URI" --file server/mongodb_schema.js
```

如果本机没有 `mongosh`，安装 MongoDB Shell 后再执行；不需要把 `mongosh` 全局写入项目依赖。

## 数据迁移边界

迁移脚本尚未执行。正式迁移前需要确认：

- 迁移的是当前 `storage/datapp.db` 全量数据，还是只迁移 `contents / analyses / kb_*`；
- 是否保留原始文件在本地 `storage/raw`，MongoDB 只存 `storage_path + sha256`；
- Atlas 是否启用 Vector Search；当前 `kb_chunks.embedding` 维度固定为 768；
- 是否先做 SQLite → MongoDB 校验，再切换应用读写。

## 来源

- MongoDB Atlas 连接：https://www.mongodb.com/zh-cn/docs/atlas/connect-your-application/
- MongoDB Python Driver：https://www.mongodb.com/zh-cn/docs/drivers/python/
- Atlas Vector Search：https://www.mongodb.com/zh-cn/docs/atlas/atlas-search/vector-search/vector-search-overview/
