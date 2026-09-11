# datapp 数据库契约 · app 端对接说明

> **读者**：app 端（Android，`app/`）的 agent / 开发者。
> **目的**：server ↔ app 双通的对接依据。app 侧的假数据层（`data/TempData.kt`）
> **已于 2026-09-11 整份删除** —— 现在 app 的每一个数字都来自服务端。
> **体检基线**：2026-09-10 首检 / 2026-09-11 复检，真库 `F:\datapp\storage\datapp.db`（13 张业务表 + 13 个索引，全部检查 PASS，**列定义 0 漂移**）。
> 复检时 `kb_documents.status` 默认值漂移已由重建表迁移修掉，见 §8.2。
> **复现命令**：`cd server && uv run python tools/verify_schema.py`

---

## 0. 结论先行

结构**健康**，可以开始对接。原报告中的红线越线**已在服务端闭环修复**（双层门禁，见 §8.1），
原报告的 1 处默认值漂移**已修复**（重建表迁移，见 §8.2），真库与 `schema.sql` 现在逐列一致。

| 项目 | 结果 |
|---|---|
| 13 张业务表齐全 | PASS |
| 列缺失 / 多余列 | 无 |
| 索引与 `schema.sql` 一致（13 个） | PASS |
| `PRAGMA integrity_check` | ok |
| `PRAGMA foreign_key_check` | 干净（0 违规） |
| 孤儿行（7 组外键关系） | 0 |
| 15 个 JSON 列可解析且顶层类型正确 | PASS |
| 9 个枚举列取值合法 | PASS |
| 向量维度 | 统一 768（27 个 chunk，全部可检索） |
| `chunk_count` 与实际分块数 | 一致 |
| 列定义漂移 | **无**（`kb_documents.status` 已由重建表迁移修复，见 §8.2） |
| 红线（未审核内容不得进 `kb_chunks`） | **0 违反**（数据层 + 检索层双层门禁已闭环） |
| **app ↔ 后端数据连通** | **已打通**（模拟器实测：真实数据 + 封面图 200，见 §1.3 / §1.4） |
| **app 全功能继承**（分析 / 审核 / 报告 / 知识库问答 / 向量化） | **已落地**，写操作逐个实测通过（清单见 `docs/开发日志.md` §四） |
| 采集 / 浏览器连接 | **不继承** —— 依赖桌面端 OpenCLI 的已登录浏览器会话，手机没有（设计边界） |
| **app 端「删除」= 软隐藏** | **已落地**：只打 `app_hidden_at` 标记，web 看板照常可见并可一键恢复，见 §3.4 |

---

## 1. 连接与认证

### 1.1 地址（**手机直连本地后端；app 内可改，不用重编**）

| 环境 | Base URL | 本机实测 |
|---|---|---|
| 本机浏览器 / 看板 | `http://127.0.0.1:8000` | ✅ |
| **真机 / 模拟器（同一局域网）** | **`http://<电脑局域网IP>:8000`**（当前 `192.168.0.109`） | ✅ |
| 模拟器 / USB 真机（`adb reverse`） | `http://127.0.0.1:8000` + `adb reverse tcp:8000 tcp:8000` | ✅ |
| Android 模拟器（宿主机别名） | `http://10.0.2.2:8000` | ❌ **本机不通**，见下 |

后端已监听 `0.0.0.0:8000`，Windows 防火墙三档（域/专用/公用）**均为关闭**，
手机与电脑同一网络时可直接访问 —— 实测走局域网地址 `/health` 返回 200。

> ⚠ **IP 由 DHCP 分配，换网络会变。** 每次联调前先确认当前地址：
> ```bash
> ipconfig | grep -A5 "WLAN"   # 找"WLAN"或"以太网"那块网卡的 IPv4，当前为 192.168.0.109
> ```

#### 地址怎么给 app（**运行时改，不再需要重编**）

`DatappApiClient.baseUrl` 是 public `var`，`openConnection` 每次请求现读，
所以改它即时生效。三级取值，后者被前者覆盖：

1. **用户在本机设的地址**（`SessionStore.baseUrl`，存 SharedPreferences）—— 平时走这条
2. 编译期默认 `BuildConfig.DATAPP_BASE_URL` ← 根 `gradle.properties` 的 `datapp.baseUrl`
   （现为中性兜底 `http://127.0.0.1:8000`）
3. 都没有 → 用 2

**app 内的入口有两处，都指向同一个对话框**（
[BaseUrlEditor.kt](../app/src/main/java/com/example/datapp/ui/components/BaseUrlEditor.kt)）：

- 「我的」→ 连接 → **修改地址**
- **登录页 → 「连不上？改服务器地址」**

登录页那处不是可选的：Cookie 模式下地址若填错，冷启动必然停在登录页，而「我的」页
要会话建立之后才可达 —— 没有它，用户会被**锁死在登录页**，连能改地址的地方都找不到。

对话框里有两个按钮：**测试连接**（探输入框里那个）、**自动探测**（并发探候选列表，
命中即填回输入框，仍需手动确认保存）。候选 = 输入值 → **打开对话框时正在生效的地址**
→ 编译期默认 → `10.0.2.2:8000` → `127.0.0.1:8000`；**不做网段扫描**（DHCP 换到别的
网段时一个都命中不了，只能手填，别把「自动探测」当万能）。

两个约束写进了代码注释，改这里之前先读：

- **探针必须用根路径 `/health`**，不要改成 `/api/v1/health` —— 后者会 spawn OpenCLI
  子进程做真实登录态探测（冷缓存实测 8.9s、偶发 >30s，没装采集工具时直接 500），
  拿它做探针会把「服务好得很，只是探针慢」误判成「这个地址不通」。
- **探测一律用临时 `DatappApiClient(候选地址)`**，绝不改正在生效的那个 client ——
  否则测一个坏地址会顺手把当前会话打挂，用户连「取消」都救不回来。

> **`10.0.2.2` 在本机不通**（2026-09-11 实测 `failed to connect to /10.0.2.2 (port 8000)
> from /10.0.2.15 ... after 8000ms`，是**超时不是拒绝**）。本机有 TUN 代理网卡
> `198.18.0.1`，大概率是它或防火墙搅的。别把它当"永不漂移的稳定选项"；换台机器
> 可能就通了，所以候选列表里留着它，但不要依赖。
>
> 想彻底不受网络环境影响，就用 `adb reverse tcp:8000 tcp:8000` 配 `127.0.0.1` ——
> 走 adb 自己的 socket 转发，不碰网络栈，模拟器和 USB 真机都能用。缺点是每次
> adb 断连要重跑。

### 1.2 Android 明文 HTTP（**接入必踩**）

Android 9（API 28）起默认**禁止明文 HTTP**，访问 `http://192.168.10.145:8000` 会被系统直接拦截：

```
java.net.UnknownServiceException:
CLEARTEXT communication to 192.168.10.145 not permitted by network security policy
```

二选一：

**A. 调试期快速放行** —— `AndroidManifest.xml` 的 `<application>` 上加：
```xml
android:usesCleartextTraffic="true"
```

**B. 只放行局域网地址**（推荐，`app/src/main/res/xml/network_security_config.xml`）：
```xml
<network-security-config>
  <domain-config cleartextTrafficPermitted="true">
    <domain includeSubdomains="true">192.168.10.145</domain>
  </domain-config>
</network-security-config>
```
manifest 里引用：`android:networkSecurityConfig="@xml/network_security_config"`。

### 1.3 认证（两条通道；**app 走设备令牌，免账号密码**）

服务端并行支持两条通道，按需要选：

| | **管理员 Cookie** | **设备令牌（app）** |
|---|---|---|
| 凭证 | 登录后下发的签名 Cookie | 请求头 `X-Datapp-App-Token: <token>` |
| 读 | ✅ | ✅ |
| 写 | ✅ | ✅ |
| 采集模块 | ✅ | ❌ **401** |
| 泄露后果 | 改 / 删 / 触发采集 | 改 / 删（**不含采集**） |
| app 需否存密码 | 需要 | **不需要** |
| 当前使用方 | Web 看板 | **✅ app 主力通道**（§1.3.1） |

> **为什么 app 不用管理员密码**：app 要继承的功能里**一半是写操作**（审核 / 重析 /
> 报告 / 入库 / 向量化 / 删除），只读凭证撑不起来。设备令牌 = 管理员**减去采集模块**，
> 既保住全部功能，又把「可被反编译的令牌」与「触发真实平台采集」隔开。
>
> **两条通道任一通过即放行，全不通过 → 401**（`server/app/main.py` 中间件）。
> 没有优先级顺序 —— 两个凭证可以同时挂在同一个请求上（见 §1.3.2）。

#### 1.3.1 设备令牌（app 主力通道，2026-09-11 起）

- **启动零输入**：`DatappApp.kt` 的 `LaunchedEffect(Unit)` 首行判断
  `BuildConfig.DATAPP_APP_TOKEN` 非空 → 直接进主界面并 `syncAll()`，**连探测请求都不发**。
- 令牌从 `local.properties` 的 `datapp.appToken` 经 `buildConfigField` 注入
  `BuildConfig.DATAPP_APP_TOKEN`，随每个请求挂在 `X-Datapp-App-Token` 头。
- **排除采集**：`/api/v1/collection-runs`、`/api/v1/browser/*` 一律 401
  （`app/auth.py::_APP_TOKEN_DENY_PREFIXES`）。用**前缀匹配**而非 `(method, path)` 精确匹配
  —— `/collection-runs/{run_id}/cancel` 含动态段，精确匹配覆盖不到。
- **「退出登录」按钮在令牌模式下不渲染**（`ProfileScreen(canLogout=)`）：没有会话可退，
  点了下个请求照样带令牌放行，留着只会误导。
- **恢复 app 端隐藏仅管理端可做**：`DELETE /api/v1/contents/{id}/app-hidden` 对设备令牌 **401**
  （`app/auth.py::_APP_TOKEN_DENY_DELETE_SUFFIXES`，按方法 + 路径**后缀**匹配）。
  隐藏自身（`POST` 同一路径）仍放行 —— 那是 app 的本职功能。若 app 能自行恢复，
  点一下「删除」再点一下「恢复」就绕过了整套可见性机制。见 §3.4。

**服务端配置**（`server/.env`）：
```
DATAPP_APP_TOKEN=<高熵随机串>
```
未设置 → 该通道关闭，app 回落登录页。比较用 `secrets.compare_digest`
（`app/auth.py::app_token_ok`），防时序侧信道。

**app 侧配置**（根项目 `local.properties`，与 `sdk.dir` 同级，**不入版本控制**）：
```
datapp.appToken=<同一个串>
```

> **令牌是编进 APK 的** —— 反编译可取，这是「app 不输密码」的固有代价。
> 排除采集模块把后果限制在「改库里的数据」，不触及「触发真实平台采集」。
> 如需吊销：服务端换 `DATAPP_APP_TOKEN`，app 侧同步 `datapp.appToken` 后重装。

#### 1.3.2 兜底登录流程（Cookie，**仅在本机未配置设备令牌时生效**）

- **启动探测**：`GET /api/v1/auth/session` 探测本地 Cookie 是否仍有效；
  有效 → 直接进主界面并同步数据；无效 → 进登录页。
- 登录 `POST /api/v1/auth/login` 成功后，`Set-Cookie` 由 app 自己解析并持久化
  （原生 `HttpURLConnection`，无 `CookieJar`），用户名与 Cookie 存 `SessionStore`。
- **任意请求收到 401** → 立即清会话、回登录页，不静默重试。
- 会话 12 小时过期。

> 这条通道**刻意保留**：令牌没配 / 配错时 app 落回登录页仍能用，不至于变砖。
> 实测（清空 `datapp.appToken` 重编装包）确认回落正常，`admin` 仍可登录。

#### 1.3.3 请求头怎么挂

`DatappApiClient.applyAuth()` 把两个头**同时挂**（两个独立 `if`，不互斥）：
`X-Datapp-App-Token`（配了才挂）与 `Cookie`（登录过才有）。服务端任一通道通过即放行。

> **实现更正**：**不是**「Cookie 优先、没有才回落到令牌」这种互斥关系。
> 本文档早期版本写成了后者，与实现不符，以本节为准。
> 两者都不挂 → 服务端 401，失败显式暴露，不做静默降级。

#### 门禁范围与例外

| 项 | 值 |
|---|---|
| API 前缀 | `/api/v1` |
| 门禁范围 | 所有 `/api/v1/*` |
| **例外** | `/api/v1/auth/*`、`/api/v1/health` |
| 未认证响应 | HTTP 401 `{"detail": "需要管理员登录"}` |
| 静态资源 | `/media/*`（封面图）**免认证**，可直接加载 |

#### 实测（走局域网地址 `192.168.10.145:8000`）

| 请求 | 结果 |
|---|---|
| `GET /health` | 200（免认证） |
| `GET /api/v1/contents?limit=3` **不带 token** | 401 |
| `GET /api/v1/contents?limit=3` **带正确 token** | 200 |
| `GET /api/v1/reports?limit=3` **带正确 token** | 200 |
| `GET /api/v1/contents?limit=3` **带错误 token** | 401 |

**端到端实测**（2026-09-10，`emulator-5554` 装 debug APK）：
app 启动后服务端日志出现
`192.168.10.145 - "GET /api/v1/contents?limit=20" 200`、
8 条 `GET /api/v1/contents/{id}/analyses` 200、
`GET /api/v1/reports?limit=20` 200，
以及 `GET /media/*.webp 200` —— **封面图真实加载**，
界面数值为真实库数据（有效内容 8 条 / 待复核 1 条），不再是 `TempData` 的假数据。

#### 写操作（审核改判 / 重审 / 报告 / 入库 / 向量化 / 删除）已接入

2026-09-11 起 app 全部写操作走管理员 Cookie，逐个实测通过（清单见
`docs/开发日志.md` §四）。接入时踩到的四个点，将来换实现也别踩：

1. `POST /api/v1/auth/login`，body `{"username":"admin","password":"<密码>"}`
2. **必须自己保存 `Set-Cookie`** —— 无论 OkHttp 还是原生 `HttpURLConnection`
   都**不跨请求保存 Cookie**，不存则登录响应里的 `Set-Cookie` 被丢掉，后续每个请求仍然 401
3. Cookie 名 `datapp_admin_session`（`httponly` / `samesite=lax` / `path=/` / `max_age=12h`）
4. 会话 12 小时过期 —— 收到 401 应清会话跳登录页，而非静默失败

```bash
# 设备令牌取数据（app 通道）
curl -H "X-Datapp-App-Token: <token>" "http://192.168.10.145:8000/api/v1/contents?limit=20"

# 管理员登录并保存 Cookie
curl -c jar.txt -X POST http://192.168.10.145:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"<你的密码>"}'

# 带 Cookie 取数据
curl -b jar.txt "http://192.168.10.145:8000/api/v1/contents?limit=20"

# 验证写操作（如创建采集任务）
curl -b jar.txt -X POST "http://192.168.10.145:8000/api/v1/collection-runs" \
  -H "Content-Type: application/json" -d '{"platform":"xhs","max_items":5}'
```

### 1.4 封面图（易错点）

用 `contents.cover_local`，形如 `/media/6a27827b0000000021020c96.webp`。
app 端拼 `BASE_URL + cover_local` 即可，**免认证**，实测返回 `200 image/webp`。

> ⚠ **不要用 `cover_url`。** 那是 xhscdn 的**带时效签名 URL**（形如
> `http://sns-webpic-qc.xhscdn.com/202609100217/...`），**过期即 403**。
> `cover_local` 是本地落盘副本（`storage/media/`），永久有效。

---

## 2. 表结构契约

> **行数口径**：各小节标题里的行数是 **2026-09-10 快照**，会随采集与审核持续变动，
> 以实际查询为准（`SELECT COUNT(*) FROM <表>`）。**列定义才是契约，行数只是参考。**

**时间格式**：所有 `*_at` / `created_at` 列均为 **ISO-8601 UTC 字符串**（例 `2026-09-09T16:39:19.574869+00:00`），
`TEXT` 存储，按**字符串排序即时间排序**。app 端解析用 `OffsetDateTime.parse`。

**JSON 列**：`_` 后缀为 `TEXT` 存储的 JSON 文本，app 端自行 `json.decode`。下方标注了顶层类型。

### 2.1 `contents` — 内容池（采集落地表，20 行）

| 列 | 类型 | 说明 |
|---|---|---|
| `content_id` | TEXT PK | 业务主键，格式 `{platform}:{platform_item_id}`，例 `xhs:6907ba84000000000302ccbb` |
| `platform` | TEXT NOT NULL | 目前只有 `xhs` |
| `platform_item_id` | TEXT NOT NULL | 平台侧 id |
| `content_type` | TEXT | `note` \| `video` \| `image` \| `mixed`，可空 |
| `canonical_url` | TEXT | 原帖地址 |
| `author_id` / `author_name` | TEXT | 作者 |
| `published_at` | TEXT | 发布时间（可空） |
| `collected_at` | TEXT NOT NULL | 采集时间 |
| `title` / `text` | TEXT | 标题 / 正文，可空 |
| `cover_url` | TEXT | 远程首图 |
| `cover_local` | TEXT | 已下载封面，形如 `/media/xxx.jpg`，**空则退化用 `cover_url`** |
| `tags` | TEXT JSON **array** | 去 `#` 的标签 |
| `media` | TEXT JSON **array** | 媒体列表 |
| `engagement` | TEXT JSON **object** | 互动快照，计数值为 int |
| `raw_refs` | TEXT JSON **array** | 原始层引用 |
| `review_status` | TEXT NOT NULL DEFAULT `pending` | **见 §3.1 状态机** |
| `active_batch_id` | TEXT NULL | 生效审核批次，**NULL 是常态**（见 §4） |
| `app_hidden_at` | TEXT NULL（迁移列） | **app 端隐藏标记**，NULL = 正常可见（见 §3.4） |
| UNIQUE | | `(platform, platform_item_id)` |

> app 端列表页对应 `TempData.insights` / `reports`，真实来源是 `contents` + `analyses` + `reports`。

### 2.2 `claims` — 审核断言（30 行）

| 列 | 类型 | 说明 |
|---|---|---|
| `claim_id` | TEXT PK | |
| `content_id` | TEXT NOT NULL → `contents.content_id` | |
| `text` | TEXT NOT NULL | 断言原文 |
| `status` | TEXT NOT NULL DEFAULT `unverified` | `unverified` \| `supported` \| `contradicted` \| `unclear` |
| `confidence` | REAL | 0–1，可空 |
| `claim_type` | TEXT | `fact` \| `opinion` \| `prediction` \| `promotion`，可空 |
| `meta` | TEXT JSON **object** | `entities` / `time_scope` 等 |
| `batch_id` | TEXT NOT NULL DEFAULT `''` | **审核批次**，`''` = 迁移前的第 1 版 |
| `batch_seq` | INTEGER NOT NULL DEFAULT 1 | 批次序号，从 1 递增 |
| `created_at` / `updated_at` | TEXT NOT NULL | |

### 2.3 `evidence` — 证据（1 行）

`evidence_id` PK · `claim_id` → `claims` · `source_kind` NOT NULL · `source_ref` ·
`excerpt` NOT NULL DEFAULT `''` · `supports` INTEGER（可空，1/0）· `strength` REAL NOT NULL DEFAULT 1.0 · `collected_at`。

### 2.4 `review_decisions` — 人工/规则改判（49 行）

`decision_id` PK · `claim_id` → `claims` · `status` NOT NULL · `rationale` NOT NULL DEFAULT `''` ·
`reviewer` NOT NULL DEFAULT `'rule-engine'`（人工为 `'human'`）· `evidence_ids` JSON array · `created_at`。

### 2.5 `analyses` — 作品分析（13 行）

| 列 | 类型 | 说明 |
|---|---|---|
| `analysis_id` | TEXT PK | |
| `content_id` | TEXT NOT NULL → `contents` | |
| `verdict` | TEXT NOT NULL DEFAULT `uncertain` | `viral` \| `flat` \| `uncertain` |
| `payload` | TEXT JSON **object** | 结构化分析结果 |
| `markdown` | TEXT NOT NULL DEFAULT `''` | 审计用 Markdown 全文 |
| `compared_with` | TEXT JSON **array** | 真实基线 content_id 列表 |
| `focus` | TEXT NOT NULL DEFAULT `''` | 重析时用户注入的补充视角，`''` = 标准分析 |
| `created_at` · `schema_version` DEFAULT `'1.0'` | | |

**版本化**：同一 `content_id` 可有多条，**新→旧倒序**即版本序（最新=第 N 版）。

### 2.6 `reports` — 报告（4 行）

`report_id` PK · `title` NOT NULL · `content_ids` JSON **array** · `payload` JSON **object** ·
`markdown` NOT NULL DEFAULT `''` · `created_at` · `schema_version` DEFAULT `'1.0'`。

⚠ **`content_ids` 是 JSON 数组文本，没有外键**。删除内容时报告**摘除该 id 而非整篇删除**（摘空才删）。
app 端按内容查报告要用 `GET /reports?content_id=`，不要自己 `LIKE`（服务端已做 `LIKE '%"id"%'` 粗筛 + Python 精确成员判定，防 `xhs:aaa` 误命中 `xhs:aaa-extra`）。

### 2.7 `kb_documents` — 知识库文档（6 行）

| 列 | 类型 | 说明 |
|---|---|---|
| `doc_id` | TEXT PK | |
| `source_type` | TEXT NOT NULL | `analysis` \| `manual` \| `content` |
| `source_id` | TEXT NULL | `content_id` / `analysis_id`；`manual` 置空（靠 hash 去重） |
| `doc_type` | TEXT NOT NULL DEFAULT `general` | `general`（markdown 切片）\| `qa`（问答对，见 `kb_qa_pairs`） |
| `title` NOT NULL · `author` · `url` · `tags` JSON array NOT NULL DEFAULT `[]` | | |
| `content_hash` | TEXT NOT NULL | `sha256(markdown)`，**幂等去重键** |
| `raw_text` / `markdown` | TEXT NOT NULL DEFAULT `''` | 入库前原文 / 清洗后 markdown（向量化入口） |
| `status` | TEXT NOT NULL DEFAULT `pending` | `pending` \| `embedding` \| `ready` \| `failed`（真库默认值已对齐，见 §8.2） |
| `chunk_count` | INTEGER NOT NULL DEFAULT 0 | 与实际 `kb_chunks` 行数一致（已验证） |
| `created_at` | TEXT NOT NULL | |

### 2.8 `kb_chunks` — 向量分块（27 行）

`chunk_id` PK · `doc_id` → `kb_documents` · `chunk_index` INTEGER NOT NULL · `text` NOT NULL ·
`embedding` JSON **array**（float，**统一 768 维**）· `modality` NOT NULL DEFAULT `'text'`（`text`\|`image`）·
`image_url` · `meta` JSON **object** · `created_at`。

### 2.9 `kb_qa_pairs` — Q&A 知识原子（13 行）

`qa_id` PK · `doc_id` → `kb_documents` · `qa_index` DEFAULT 0 · `question` NOT NULL ·
`answer` DEFAULT `''` · `dimensions` JSON **object**（六维：`technique`/`persona`/`hook`/`structure`/`transfer`/`transfer_risk`）·
`evidence` JSON **array** · `tags` JSON **array** · `source_type` DEFAULT `'manual'`（`distilled`\|`manual`）·
`source_id`/`source_url`/`source_author` · `status` DEFAULT `'draft'`（`draft`\|`approved`\|`rejected`）· `created_at`/`updated_at`。

### 2.10 `collection_runs` / `collection_events` / `raw_assets`

- `collection_runs`（8 行）：`id` PK · `request_id` · `platform` NOT NULL · `target_type` NOT NULL
  (`url`\|`keyword`\|`account`\|`post`) · `target` NOT NULL · `max_items` DEFAULT 20 · `status` NOT NULL
  (`queued`\|`running`\|`success`\|`partial`\|`blocked`\|`failed`\|`cancelled`\|`retry_wait`) ·
  `started_at`/`finished_at` · `error_code` · `error_category` (`network`\|`timeout`\|`auth`\|`rate_limit`\|`argument`\|`empty`\|`parse`) ·
  `latency_ms` INTEGER（`finished_at - started_at` 的毫秒差，历史行为 NULL）·
  `items_found`/`items_saved`/`retry_count`/`rate_limit_signal` DEFAULT 0 · `tool_version`。
- `collection_events`（32 行）：自增 `id` · `run_id` NOT NULL · `seq` DEFAULT 0 · `ts` NOT NULL ·
  `level` NOT NULL (`info`\|`warn`\|`error`) · `category` · `code` · `message`。**无外键约束**，但实际无孤儿。
- `raw_assets`（7 行）：`id` PK · `run_id` · `content_id` · `kind` (`search`\|`note`\|`comments`\|`feed`\|`user`) ·
  `mime` · `sha256` · `bytes` DEFAULT 0 · `storage_path` · `collected_at`。

### 2.11 `collection_requests` — app 采集申请（4 行）

app 只能「提交意图」，不能直接驱动采集（设计边界，见 §1.2）。

`id` PK · `platform` DEFAULT `'xhs'` · `target_type` DEFAULT `'keyword'`（app 只发 keyword）·
`target` NOT NULL · `max_items` DEFAULT 10 · `status` DEFAULT `'pending'`
(`pending`\|`approved`\|`rejected`) · `requested_by` DEFAULT `'app'` (`app`\|`web`) ·
`run_id`（审批通过并派发后回填 `collection_runs.id`）· `decided_at`/`decided_by` · `note` · `created_at`。

索引 `idx_coll_req_status(status, created_at)`。**无外键约束**（`run_id` 是软引用）。

---

## 3. 状态机

### 3.1 `contents.review_status`

```
pending ──审核──> extracted ──┬─> approved    全部断言 supported
                              ├─> rejected    存在 contradicted
                              └─> extracted   其余（存疑/未定）
                      
blocked   命中风控 / 验证码 / 403 时的强制态
```

**当前真库分布**：`pending` 13 · `extracted` 4。**尚无 `approved`**。

### 3.2 `kb_documents.status`

```
manual 文档：  pending ──向量化──> embedding ──> ready
content/analysis 系统产物：     一步到位 ready
失败： failed
Q&A 文档被编辑：ready ──回落──> pending（改动必须重新向量化才生效）
```

### 3.3 `kb_qa_pairs.status`

```
draft ──approve──> approved ──（被改写）──> 自动回落 draft
      └─reject───> rejected
```
**只有 `approved` 的问答对会被向量化。**

### 3.4 `contents.app_hidden_at` —— app 端隐藏（2026-09-11 起）

```
NULL  ──POST /contents/{id}/app-hidden──>  时间戳（app 端不再可见）
      <──DELETE /contents/{id}/app-hidden──  NULL（app 端恢复可见，仅管理员可调）
```

**两条通道看到的数据集不同**，这是服务端按通道过滤的结果，**不是客户端藏起来**（绕过 UI 直接
`curl` 也一样拿不到）：

| | app 通道（设备令牌 / 只读令牌） | web 通道（管理员 Cookie） |
|---|---|---|
| 已隐藏内容 | 列表 / 概览计数 / 详情 / 报告 / 审核 / 分析 / KB 文档 / **检索命中 / 问答引用** 全部不可见 | 照常可见，响应带 `app_hidden_at` |
| 隐藏内容的详情 | `404`（**不是 403** —— 403 等于确认「这条存在」） | `200` |
| 可执行动作 | 隐藏 | 取消隐藏、彻底删除（`DELETE /contents/{id}` 未改动） |

**派生数据不单独打标记**，可见性**继承自源 content**：一条内容被隐藏，它的断言 / 证据 / 分析 /
报告 / KB 文档 / QA 对在 app 通道一并消失；取消隐藏时**原子恢复**，不存在「内容回来了但 KB 文档
还查不到」的半残态。**报告是「任一被引用内容隐藏即整篇隐藏」**（报告正文逐字拼了各内容标题与
摘要，无法表达「部分隐藏」）。

**隐藏不删数据**：`kb_chunks` 物理行、磁盘封面、断言全文都在，恢复后一切照旧。设备令牌**不能**
取消隐藏（`DELETE .../app-hidden` 对它是 401），恢复权只归管理端。

---

## 4. 审核版本化语义（v2026-09-10 起）

这是本次改动最大的部分，app 端必须理解，否则会读错数据。

- **重审 = 新增批次，不覆盖**。`POST /contents/{id}/review?force=true` 产生新的
  `batch_id` / `batch_seq = max+1`，**旧批次的行一条都不删**。
- **生效批次**：`contents.active_batch_id`。
  - **非 NULL** → 就是它。
  - **NULL**（当前 17 条里 16 条如此）→ 回落为**该内容 `batch_seq` 最大的批次**。
- **零回填**：迁移前的历史行是 `batch_id = ''`、`batch_seq = 1`，天然就是「第 1 版」。
  真库现状：第 1 版 3 个内容 / 7 条断言；第 2 版 1 个内容 / 7 条；第 3 版 1 个内容 / 7 条。
- **`GET /contents/{id}/claims` 默认只返回生效批次**；要读历史版本传 `?batch_id=`。
- `claims.batch_id ↔ batch_seq` 已校验为**一对一**。

app 端做「版本选择」UI 时，对应关系：

| app 概念 | 后端 |
|---|---|
| 分析的第 N 版 | `GET /contents/{id}/analyses` 返回数组的下标（倒序） |
| 审核的第 N 版 | `GET /contents/{id}/review-batches` 的 `batch_seq` |
| 报告的第 N 版 | `GET /reports?content_id=` 返回数组的下标 |

**入库前必须让用户确认版本** —— 分析入库走 `POST /api/v1/kb/analyses/{analysis_id}`（指向具体某一版），
不是「入库最新版」。已入库的版本在分析响应里 `in_kb = true`。

---

## 5. 双通接口面（服务端权威路由表）

> **口径**：本表以**服务端实际注册的路由**为准（`server/app/api/routes/*.py`，共 **48 个**）。
> 「看板在用」列标注前端 `client/src/api/client.ts` 是否实际调用 —— app 端**照着实现即可**，
> 标 ✗ 的对应用户需求价值低，可后置。
> **路径前缀统一 `/api/v1`**；除 `/health` 与 `/api/v1/auth/*` 外**全部需要认证**，
> 两条通道任一通过即可（见 §1.3）。注意 `/collection-runs` 与 `/browser/*` 对
> **设备令牌**额外关门 —— app 走这两个前缀会拿到 401。

### 5.1 CORS：原生请求不用管，WebView 必须管

看板跑在 `5173`，靠 Vite 代理把 `/api` 转发到 `127.0.0.1:8000`（`client/vite.config.ts`），
**始终同源**，所以前端从未遇到跨域问题。

app 直连 `http://192.168.10.145:8000` 时：

- **原生 HTTP 客户端（OkHttp / Retrofit）→ 不受 CORS 约束。** CORS 是浏览器机制，
  原生请求不发 `Origin` 头，服务端也不会拦。**推荐这条路。**
- **WebView 里跑 JS fetch → 必被 CORS 拦。** 服务端**当前没有 CORS 中间件**，
  届时需要另加 —— 少一层坑，**别用 WebView 套壳**。

### 5.2 认证

| 方法 | 路径 | 说明 | 看板在用 | app 在用 |
|---|---|---|---|---|
| POST | `/auth/login` | `{username,password}` → Set-Cookie `datapp_admin_session` | ✅ | 仅兜底路径 |
| GET | `/auth/session` | 启动时校验登录态 | ✅ | 仅兜底路径 |
| POST | `/auth/logout` | 销会话 | ✗ | ✗ |

> **app 默认不调这三个接口** —— 走设备令牌（`X-Datapp-App-Token`，见 §1.3.1），
> 启动零输入直进主界面。只有本机**未配置**设备令牌时才回落到这套 Cookie 登录（§1.3.2）。

### 5.3 浏览器连接（OpenCLI）

| 方法 | 路径 | 说明 | 看板在用 |
|---|---|---|---|
| GET | `/browser/status` | 连接状态（看板 10s 轮询） | ✅ |
| POST | `/browser/connect` | 发起连接 | ✅ |
| POST | `/browser/retry` | 重试连接 | ✅ |

> ⚠ 这三个端点会在**本地后端**拉起 OpenCLI 子进程 —— 即「**用手机遥控本机采集**」。
> 局域网直连场景下这是合理的：采集仍在本机执行、仍走已登录浏览器会话、仍受限速与熔断约束。
> 实测 `/api/v1/health` 返回 `{"ok":true,"logged_in":true,"username":"Melody","lc_tracing":false}`。

### 5.4 采集任务

| 方法 | 路径 | 说明 | 看板在用 |
|---|---|---|---|
| GET | `/collection-runs` | 任务列表（忙时 2s / 闲时 10s 轮询） | ✅ |
| GET | `/collection-runs/{run_id}` | 任务详情（事件时间线） | ✅ |
| POST | `/collection-runs` | 建任务 `{target_type, target, max_items}` | ✅ |
| POST | `/collection-runs/{run_id}/cancel` | 取消运行中/排队任务 | ✅ |

`target_type` 枚举：`keyword`（关键词搜索）/ `url`（笔记 URL）/ `account`（账号内容）/ `post`（笔记）。

### 5.5 内容池

| 方法 | 路径 | 说明 | 看板在用 |
|---|---|---|---|
| GET | `/contents?limit=20` | 内容列表（10s 轮询） | ✅ |
| GET | `/contents/summary?limit=20` | **派生计数，一次拿全，替代 N+1** | ✅ |
| GET | `/contents/{content_id}` | 内容详情 | ✗ |
| DELETE | `/contents/{content_id}` | 删内容 + 全部下游（9 张表 + 磁盘封面），**不可逆** | ✅ |
| POST | `/contents/{content_id}/app-hidden` | **app 端「删除」**：打隐藏标记（软隐藏），返回 204 | ✅ |
| DELETE | `/contents/{content_id}/app-hidden` | **取消隐藏**（web「重新同步到 app 端」），返回 204。**设备令牌被 401，仅管理员 Cookie 可调** | ✅ |

> **app 端「删除」不是真删** —— 只往 `contents.app_hidden_at` 写一个时间戳，内容与它的断言 /
> 证据 / 分析 / 报告 / KB 文档 / QA 对**全部原样保留**，web 看板照常可见、可一键恢复。
> 详见 §3.4。

`ContentSummary` 字段：
```
content_id, analysis_count, latest_analysis_id, latest_analysis_at,
report_count, claim_count, review_batch_count, kb_doc_count
```

### 5.6 审核（自动 / 重审 / 人工改判 / 批次）

| 方法 | 路径 | 说明 | 看板在用 |
|---|---|---|---|
| POST | `/contents/{id}/review` | 自动审核；`?force=true` → **重新审核，新增批次** | ✅ |
| GET | `/contents/{id}/claims` | 断言 + 证据；`?batch_id=` 查指定历史批次 | ✅ |
| GET | `/contents/{id}/review-batches` | 批次列表（**看板改从 claims 响应的 `batches` 取**） | ✗ |
| POST | `/contents/{id}/review-batches/{batch_id}/activate` | 切换生效批次 | ✅ |
| GET | `/claims/{claim_id}` | 单条断言快照 | ✗ |
| POST | `/claims/{claim_id}/decision` | 人工改判 `{status, rationale, evidence_ids}` | ✅ |

> **批次列表看板的取法**：`GET /contents/{id}/claims` 的响应里已含 `batches` 数组，
> 看板不再单独请求 `/review-batches`。app 任选其一即可。
>
> **人工改判前请读 §4**：改判会**重算内容状态**；且服务端对 `supported`/`contradicted`
> 要求有证据支撑，`human` 改判若置信度不足会被自动补为 1.0（见开发日志 §十）。

### 5.7 作品分析

| 方法 | 路径 | 说明 | 看板在用 |
|---|---|---|---|
| POST | `/contents/{id}/analyze` | `{focus}`；空=常规分析，非空=**注入补充视角**。**每次新增一版** | ✅ |
| GET | `/contents/{id}/analyses` | 该内容全部版本（倒序，含 `in_kb`） | ✅ |
| GET | `/analyses/{analysis_id}` | 单条分析 | ✗ |

> **版本切换是纯前端状态**（`VersionBar`）—— 前端一次拉全量版本，本地切换，不请求后端。
> app 照此实现即可少一次往返。

### 5.8 报告

| 方法 | 路径 | 说明 | 看板在用 |
|---|---|---|---|
| POST | `/reports` | `{content_ids:[...], title}` —— 单篇或**整池跨内容报告** | ✅ |
| GET | `/reports?content_id={id}` | 该内容的报告历史 | ✅ |
| GET | `/reports/{report_id}` | 单篇报告详情 | ✗ |

> `markdown` 字段已包含在列表响应里，看板直接渲染，不再单独请求详情。

### 5.9 分析 → 知识库

| 方法 | 路径 | 说明 | 看板在用 |
|---|---|---|---|
| POST | `/kb/analyses/{analysis_id}` | 把**指定版本**的分析入库（分块 + 向量化） | ✅ |
| POST | `/kb/analyses/{analysis_id}/distill-qa` | 该版分析蒸馏为 Q&A 草稿（`?force=true` 重蒸） | ✅ |
| POST | `/kb/contents/{content_id}` | 采集内容直接入库 | ✗ |
| POST | `/kb/contents/{content_id}/distill-qa` | 采集内容蒸馏 Q&A | ✗ |

> 后两个（✗）虽未被看板调用，但**服务端已设门禁**：源内容非 `approved` 会**被拒绝**（见 §8.1）。

### 5.10 知识库 —— 文档

| 方法 | 路径 | 说明 | 看板在用 |
|---|---|---|---|
| GET | `/kb/documents?limit=100` | 文档列表（含 `markdown`/`raw_text`，可直接预览） | ✅ |
| GET | `/kb/documents/{doc_id}` | 单文档 | ✗ |
| POST | `/kb/upload` | **文件上传**（multipart，字段名 `file`） | ✅ |
| POST | `/kb/documents` | 手动文段入库 `{title, text, tags, url}` | ✅ |
| POST | `/kb/documents/{doc_id}/vectorize` | 单文档向量化 | ✅ |
| POST | `/kb/documents/vectorize` | 批量向量化 `{doc_ids}` | ✅ |
| DELETE | `/kb/documents/{doc_id}` | 删单文档 | ✅ |
| POST | `/kb/documents/delete` | 批量删除 `{doc_ids}` | ✅ |

### 5.11 知识库 —— 检索与问答

| 方法 | 路径 | 说明 | 看板在用 |
|---|---|---|---|
| POST | `/kb/search` | 语义检索 `{query, top_k}` | ✅ |
| POST | `/kb/ask` | **RAG 问答** `{query, history}`，带引用，**无据拒答** | ✅ |

> `/kb/ask` 的 `answered=false` 是**正常结果**（无据拒答），不是错误，UI 别弹异常。

### 5.12 知识库 —— Q&A 蒸馏与审核

| 方法 | 路径 | 说明 | 看板在用 |
|---|---|---|---|
| POST | `/kb/qa/documents` | 手动录入 Q&A `{title, pairs:[{question,answer,tags}], tags}` | ✅ |
| POST | `/kb/qa/distill` | 纯文本蒸馏（未用） | ✗ |
| GET | `/kb/qa/documents/{doc_id}/pairs` | 查看文档的问答对 | ✅ |
| PATCH | `/kb/qa/pairs/{qa_id}` | 编辑问答对 | ✗ |
| POST | `/kb/qa/pairs/approve` | 通过 `{qa_ids}` | ✅ |
| POST | `/kb/qa/pairs/reject` | 驳回 `{qa_ids}` | ✅ |
| DELETE | `/kb/qa/pairs/{qa_id}` | 删除 | ✅ |

### 5.13 app 的假数据 → 真实来源映射

| `TempData` | 真实来源 |
|---|---|
| `Insight(title, summary, source, metric, tone, tag)` | `contents` 标题/摘要 + `ContentSummary` 计数 + `analyses.payload` 里的指标 |
| `Report(title, date, summary, points)` | `reports.title` / `created_at` / `reports.markdown`（或 `payload` 里的要点数组） |

`TempData.kt` 的 `data class` 字段可以保持不变，只把 `object TempData` 换成 Repository 即可 ——
原注释已写明这个约定。

### 5.14 纯前端状态（不需要 app 也调接口）

这些在前端**不产生网络请求**，app 端照做即可，别去找接口：

- 分析版本切换 / 报告版本切换（本地选中态）
- 复制 Markdown（`navigator.clipboard`）
- 问答历史记忆（`localStorage`，key `datapp.kb.qa-history.v1`）
- 全部可视化图表（Donut / RankBars / MiniColumns / EngagementBar）—— 由已拉取的
  `runs` / `contents` / `summaries` 本地派生
- 封面图回退：`cover_local` 加载失败时切 `cover_url`（见 §1.4，**注意时效**）

---

## 6. 写入红线（app 端必须遵守）

1. **不要直连 SQLite 改库**。真库是 server 独占写的（WAL 单写者）。
   app 端一律走 HTTP；需要新接口就在 server 侧加，不要绕过。
   若确有必要直连（只读），**必须**逐连接执行 `PRAGMA foreign_keys = ON` ——
   SQLite 默认是 **0**，不开外键约束完全不生效（本机校验连接读到 `foreign_keys = 0` 就是这个原因）。
2. **未审核内容不得进 `kb_chunks`**（项目硬红线）。服务端已双层设闸（见 §8.1）：
   入库侧校验 `review_status`、检索侧 SQL JOIN 过滤。app 端对 `review_status != approved`
   的内容调用入库接口会收到**拒绝响应** —— 这是**预期行为，不是 bug**，UI 如实提示即可。
3. **模型输出 ≠ 事实**。`analyses.payload` / `reports.payload` 是模型产物，UI 必须与已审核数据区分呈现。
4. **不伪造成功**。接口失败就显示失败；`/kb/ask` 的 `answered=false` 是正常结果（无据拒答），
   不是错误，UI 不要当成异常弹窗。
5. **删除是不可逆的**。`DELETE /contents/{id}` 会连带清 9 张表 + 删磁盘封面，
   app 端必须二次确认并展示影响面。
6. **API key 只进 `server/.env`**，绝不进 app 端代码 / 日志 / 崩溃上报。

---

## 7. 自检工具

```bash
cd server && uv run python tools/verify_schema.py
```
只读，不开写事务，逐项 PASS/WARN/FAIL，任何 FAIL 非零退出。app 端改完数据后跑一遍可确认没写坏。

```bash
cd server && uv run python tools/verify_app_hidden_e2e.py   # 服务需已在 127.0.0.1:8000 运行
```
真库 + 真服务的双通道可见性闭环（25 项）：app 令牌隐藏 → app 通道各读取面均看不到该内容及其
派生数据、web 通道照常可见且带 `app_hidden_at` → 设备令牌取消隐藏被 401 → 管理员 Cookie 恢复 →
app 通道原子重现。**`finally` 里兜底恢复，跑完不留任何 `app_hidden_at`**。改了通道判定、
仓储过滤、可见性相关路由后跑一遍。

> 存活检查走根路径 `/health`（纯内存）。**`/api/v1/health` 不能当存活探针**：它冷缓存时会
> `spawn` OpenCLI 子进程做真实登录态探测，实测 8.9s、偶发 >30s。

---

## 8. 已知问题

### 8.1 【红线】1 篇未审核内容已进 `kb_chunks` —— **已闭环修复**

> **状态更新（2026-09-10）**：越线数据**已不在库中** —— 内容 `xhs:6907ba84000000000302ccbb`
> 与 doc `2f58caafdd034fd3ad846cd7df18875d` 均查无此条，体检红线项现为 **0 违反**。
> 服务端已补上**双层门禁**：
>
> - **数据层**：`vectorize_content()` 入库前校验 `review_status`，未通过即拒绝；
> - **检索层**：`iter_retrievable_chunks()` 以 SQL JOIN 二次过滤 ——
>   脏 chunk 即便混进库，**检索也物理命中不到**。
>
> 以下为原始记录，保留备查。

#### 原始记录（已解决）

**证据**（`verify_schema.py` FAIL 项）：

```
doc_id      = 2f58caafdd034fd3ad846cd7df18875d
source_type = content
source_id   = xhs:6907ba84000000000302ccbb
title       = 完美主义者开始一本笔记本的唯一正确方式
doc status  = ready      chunk_count = 1（已向量化，embedding 768 维）
该内容 review_status = pending      ← 从未审核，claims 表里没有它的任何断言
created_at  = 2026-09-09T16:39:19Z
```

**根因**：内容入库路径没有审核门禁。`app/services/knowledge_base.py::vectorize_content()`
（`POST /api/v1/kb/contents/{content_id}`）直接读 `contents` → 切片 → 向量化，
**全程不看 `review_status`**；路由层 `knowledge_base.py` 也没有任何 `ReviewStatus` 判断。

**影响**：这篇未审核内容**可被 `POST /kb/ask` 检索到并作为引用作答** ——
即「模型输出被当作事实来源」，与项目红线直接冲突。

**建议修法**（未实施，需你拍板）：在 `vectorize_content` 入口加
`if content.review_status in ('pending', 'blocked'): raise` ，
并决定这条历史数据怎么处理（**删文档** / 保留但在检索侧过滤 / 先补审核再放行）。
**我没有动这条数据** —— 删除是不可逆的，需要你决定。

### 8.2 【已修复】`kb_documents.status` 默认值漂移

| | 值 |
|---|---|
| 老库（修复前） | `DEFAULT 'embedding'` |
| `schema.sql` | `DEFAULT 'pending'` |

原因：`CREATE TABLE IF NOT EXISTS` **永远不更新已存在表的列定义**，真库停在某个历史版本上。

**已修复**（2026-09-11）：SQLite 不支持改列默认值，只能整表重建。`app/db.py::init_db`
现在多跑一步 `_repair_column_defaults` —— 检测到真库默认值与 `schema.sql` 不一致就重建这张表
（建影子表 → 拷同名列 → 换名 → 补回索引），正常库零开销、老库只跑一次、可重复执行。
真库已执行，`verify_schema.py` A 段现在是「逐列一致」。

**仍然建议**：app 端若绕过仓储直接 `INSERT`，一律显式写 `status`。
默认值不该成为业务依据 —— 生命周期是 `pending → embedding → ready → failed`，
默认写成 `'embedding'` 会把文档永久钉在「向量化中」。

### 8.3 【设计如此】「向量化」按钮在文档 `ready` 时不出现

`kb_documents.status` 三态：`pending` → `ready`，或 `failed`。
app 只在 `pending` / `failed` 时显示「向量化」按钮 —— `ready` 文档再向量化是幂等重建，
没有意义（服务端 `vectorize_document` 本身允许，是 UI 层收的口）。

**副作用**：库里若全是 `ready` 文档，「向量化」这条路在 UI 上**无法被触发**，
联调时容易误判成「按钮没做」。要造一个 `pending` 文档，走这两条**合法**路径之一：

| 路径 | 端点 | 是否需要 LLM |
|---|---|---|
| 人工录入问答知识 | `POST /kb/qa/documents` | **不需要**（结构化终态） |
| 分析蒸馏 Q&A | `POST /kb/analyses/{id}/distill-qa` | 需要（未配 key 时 503） |

> 走人工录入通道造验证数据、验完即删，是本轮用过的做法（`docs/开发日志.md` §七.4）。
> **不要为了造测试数据去改数据库** —— 合成的问答一旦被向量化就会污染检索结果。

### 8.4 【环境】模型 API key 未配置时，LLM 依赖的操作一律 503

`_make_llm()` 惰性装配：`DATAPP_LLM_API_KEY` 为空则返回 `None`，
模型层异常被 `knowledge_base._kb_or_error` 统一映射为 **503**。

app 侧表现：`/kb/ask`（检索有命中时）、`/kb/analyses/{id}/distill-qa` 返回 503，
**错误信息原样透传**，不吞不美化。排查时先确认服务端 `DATAPP_LLM_API_KEY` 是否有效，
再怀疑 app。

---

## 附：体检原始输出（2026-09-10）

```
A. 结构完整性
  [PASS] 12 张业务表齐全: 实有 12 张
  [WARN] kb_documents.status 定义漂移: 真库=('TEXT',1,"'embedding'",0) vs schema.sql=('TEXT',1,"'pending'",0)
  [PASS] 无列缺失 / 无多余列: 漂移 1 处
  [PASS] 索引与 schema.sql 一致: 12 个索引
  [PASS] idx_claims_batch 已建（迁移列上的索引）: 老库升级路径正常
B. 数据库自身一致性
  [PASS] PRAGMA integrity_check = ok
  [PASS] PRAGMA foreign_key_check 无违规: 干净
  [PASS] journal_mode = wal
  [WARN] foreign_keys 为逐连接开关: app 端连库后必须 PRAGMA foreign_keys = ON
C. 数据不变量
  [PASS] 7 组外键关系均无孤儿
  [PASS] 15 个 JSON 列全部可解析且顶层类型正确
  [PASS] 9 个枚举列取值合法
  [PASS] claims.batch_seq 全部 >= 1
  [PASS] batch_id ↔ batch_seq 一对一
  [PASS] 审核批次分布: 第 1 版 3 内容/7 断言; 第 2 版 1 内容/7 断言; 第 3 版 1 内容/7 断言
  [PASS] contents.active_batch_id 均属于该内容自己的批次
  [PASS] 生效批次回落统计: 17 条内容，16 条 active_batch_id 为 NULL
  [PASS] kb_documents.chunk_count 与实际分块数一致
  [PASS] 向量维度统一: {768: 34}
  [FAIL] 红线：未审核/已阻断内容未进入已向量化 kb_chunks: 1 篇越线
```
