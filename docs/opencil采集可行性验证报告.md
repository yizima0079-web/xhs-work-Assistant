# OpenCLI（jackwener/opencli）采集可行性验证报告

验证日期：2026-09-09  
源码包：`F:\\下载\\OpenCLI-1.8.8.zip`  
源码目录：`F:\\datapp\\vendor\\OpenCLI-1.8.8`  
项目版本：`@jackwener/opencli@1.8.8`  
仓库：<https://github.com/jackwener/opencli>  
许可证：Apache-2.0  
压缩包 SHA-256：`D2C777B82359100891C78E7465106563BBC8C98472F627F88D6FFB49C5ED51FE`

## 1. 结论

结论：**采集主链路已在真实登录态下验证可行，状态为 PARTIAL。** 采集主链路（whoami / feed / note / comments）已实测成功；search 已修复并实测通过（见 §13）。最新动态结果见 §11，失效根因见 §12。

已确认：

- 项目实际名称为 `OpenCLI`，npm 包为 `@jackwener/opencli`。
- 项目内置 `xiaohongshu` 适配器。
- 支持搜索、问答、笔记详情、评论、Feed、用户、收藏、点赞、创作者数据、媒体下载等命令。
- 小红书适配器基于已登录 Chrome/Edge 与 Browser Bridge，不是无登录纯 HTTP 采集器。
- `note`、`comments`、`download` 要求完整签名笔记 URL，通常必须带 `xsec_token`。
- 源码内已有小红书风控检测和单次冷却重试，但没有跨 CLI 调用的全局限速器。

当前状态：

- ✅ 依赖安装（232 包）、构建（`dist/src/main.js`）、CLI 启动、Browser Bridge 连接：均通过。
- ✅ whoami / feed / note / comments：实测成功。
- ✅ search：默认与显式筛选均已修复并实测通过（§13）。
- ⏳ 未做：限速升压探测、风控信号触发验证（本轮低频只读访问未触发）。
- ⏳ search 的 likes 字段在部分显式筛选结果下返回“赞”占位文本，属字段质量待优化，不阻塞主链路。

采集主链路可进入正式数据管道开发；search 字段质量与限速/风控升压验证留待后续。

## 2. 环境检查

| 项目 | 结果 |
|---|---|
| Node.js | v24.13.0，满足 `>=20.18.1` |
| npm | 11.6.2 |
| Bun | 未安装 |
| `opencli` 命令 | 通过 `node dist/src/main.js` 运行（未全局安装） |
| `tsx`/`tsc` | 已随依赖安装，构建可用 |
| `dist/` | 已构建（`dist/src/main.js`） |
| `node_modules/` | 已安装（232 包） |
| Browser Bridge `127.0.0.1:19825` | 已连接（扩展 v1.0.24，profile `3jpz9ujj`） |
| Git 元数据 | 压缩包内未提供，无法确认 commit |

## 3. 依赖安装结果

执行：

```powershell
npm ci --ignore-scripts
npm run build
```

结果：成功（232 包，28s）。首轮曾因瞬时网络限制报 `ENOTCACHED`（缺 `zwitch-2.0.4.tgz`），registry 恢复后重试即通过。构建生成 `dist/src/main.js`，cli-manifest 1366 条。`--ignore-scripts` 用于跳过仓库 `postinstall` 的自动行为。

## 4. 已确认的小红书能力

源码位置：`clis/xiaohongshu/`。

| 命令 | 能力 | 访问方式 |
|---|---|---|
| `search` | 关键词搜索笔记，返回标题、作者、点赞、URL | Cookie/浏览器 |
| `ask` | 站内问答，返回答案与 `sources[]` | Cookie/浏览器 |
| `note` | 笔记正文、作者、点赞、收藏、评论、标签 | Cookie/浏览器 |
| `comments` | 评论及可选楼中楼回复 | Cookie/浏览器 |
| `feed` | 首页推荐流 | Cookie/浏览器 |
| `user` | 用户公开笔记 | Cookie/浏览器 |
| `creator-notes` | 创作者笔记与指标 | Cookie/浏览器 |
| `creator-stats` | 创作者数据概览与趋势 | Cookie/浏览器 |
| `download` | 下载笔记图片和视频 | Cookie/浏览器 |

推荐第一条真实验证命令：

```powershell
opencli xiaohongshu search "AI眼镜" --limit 1 -f json
```

搜索结果中的完整 URL 再用于详情验证：

```powershell
opencli xiaohongshu note "<search 返回的完整 URL>" -f json
opencli xiaohongshu comments "<search 返回的完整 URL>" --limit 3 -f json
opencli xiaohongshu download "<search 返回的完整 URL>" --output F:\\datapp\\storage\\raw\\xhs
```

这些命令只有在 Browser Bridge 已连接并且 Chrome/Edge 已登录小红书时才能执行。

## 5. 风控实现检查

`clis/xiaohongshu/risk-control.js` 已实现：

- 检测 `website-login/error`、`error_code=300017`、`error_code=300031`。
- 检测页面文本中的安全限制/访问异常提示。
- 首次命中风控后随机冷却 8–18 秒。
- 冷却后最多重新读取一次。
- 再次命中则抛出 `SECURITY_BLOCK`，不进入无限重试。
- 详情页导航后随机等待 2–5 秒。

源码注释明确指出：该机制只处理单次调用内的温和重试，不负责多个 CLI 调用之间的会话级限速。因此正式系统仍需在外层实现任务级令牌桶、并发上限、熔断和审计。

仓库另有 rate limiter plugin 文档，示例默认每次浏览器平台命令后等待 5–30 秒；该插件当前未被本次运行验证加载。

## 6. 通过/阻塞判定

| 验收项 | 状态 | 说明 |
|---|---|---|
| 仓库身份确认 | PASS | `jackwener/opencli`，包版本 1.8.8 |
| License 确认 | PASS | Apache-2.0 |
| XHS 适配器存在 | PASS | `clis/xiaohongshu/` 完整存在 |
| 图文能力 | PASS | note 实测成功（图文/视频） |
| 视频/图片下载 | STATIC PASS | 源码支持，未做真实下载 |
| 依赖安装 | PASS | `npm ci` 232 包 |
| CLI 构建 | PASS | `dist/src/main.js` |
| Browser Bridge | PASS | `127.0.0.1:19825` 已连接 |
| 小红书真实采集 | PARTIAL | whoami/feed/note/comments/search 均实测通过 |
| 限速实测 | NOT RUN | 低频访问未触发，未升压 |
| 风控实测 | NOT RUN | 未触发验证码/300017/300031 |

## 7. 下一步

在可访问 npm registry 的环境执行：

```powershell
Set-Location F:\\datapp\\vendor\\OpenCLI-1.8.8
npm ci --ignore-scripts
npm run build
```

然后：

1. 启动 Edge/Chrome，安装并启用 OpenCLI Browser Bridge 扩展。
2. 登录 `xiaohongshu.com`，确认登录态属于授权账号。
3. 检查 `http://127.0.0.1:19825` 可连接。
4. 执行单关键词、单条结果的 JSON 搜索测试。
5. 从搜索结果复制带 `xsec_token` 的 URL，执行一次 `note`。
6. 视授权和内容用途决定是否执行媒体下载；默认先只取元数据。
7. 将 stdout、退出码、耗时、URL 脱敏后保存到 PoC 记录。
8. 任何验证码、403、300017、300031 或安全限制立即停止升压测试。

只有完成上述真实验证，才能判断采集链路是否进入后续数据库、审核和 RAG 开发。

## 8. 2026-09-09 环境验证结果

本次只做本地环境检查、构建尝试、适配器语法检查和 Browser Bridge 探测，未访问小红书，未执行账号操作。

| 检查项 | 结果 |
|---|---|
| Node.js | PASS，v24.13.0 |
| npm | PASS，11.6.2 |
| Node.js 版本要求 | PASS，满足 `>=20.18.1` |
| `npm ci` | PASS，232 包 |
| `npm run build` | PASS，生成 `dist/src/main.js` |
| 小红书适配器 JavaScript 语法 | PASS |
| package/manifest JSON | PASS，可解析 |
| Edge | 存在（实际 Bridge 已连） |
| Chrome | 未发现（Bridge 走 Edge） |
| OpenCLI CLI | PASS，`node dist/src/main.js` 可运行 |
| OpenCLI `dist` | PASS，已构建 |
| Browser Bridge `127.0.0.1:19825` | PASS，已连接（v1.0.24） |

### 环境修复顺序

1. 在可访问 npm registry 的环境执行 `npm ci --ignore-scripts`。
2. 执行 `npm run build`，确认 `dist/src/main.js` 生成。
3. 按仓库扩展说明构建 extension，确认 `extension/dist/background.js` 存在。
4. 使用受支持的 Chromium 浏览器加载扩展；当前 Edge 109 过旧，优先升级 Edge 或安装 Chrome。
5. 启动 Browser Bridge，确认 `127.0.0.1:19825` 可连接。
6. 重新执行 `opencli doctor` 和 `opencli xiaohongshu search ... --limit 1 -f json`。

## 9. Browser Profile 验证结果

用户提供的 profile id：`3jpz9ujj`。

本机只读检查结果：

- Edge 当前未运行。
- `127.0.0.1:19825` 未监听，Browser Bridge daemon 未启动或未连接。
- `3jpz9ujj` 是 OpenCLI Browser Bridge 的 `contextId`，不是 Edge 的文件夹名；不能用它直接对应 `Profile 3`。
- Edge `Profile 3` 的扩展目录中当前识别到迅雷和 WPS，未识别到 OpenCLI 扩展。
- 当前无法确认 `3jpz9ujj` 是否属于本机 Edge、是否在线以及是否已登录小红书。

上述为静态只读检查（浏览器未运行时），属于历史阻断记录。第二轮实测已通过 `opencli doctor` 确认：profile `3jpz9ujj` connected（扩展 v1.0.24），授权测试账号已登录。此 BLOCKED 已解除。

## 10. 官方设计核对后的执行方向

根据仓库 README、扩展说明、Privacy Policy、XHS sitemap/API 说明和源码实现，方向修正如下：

1. OpenCLI 只作为“已登录浏览器会话采集适配器”，不承担数据库、审核、报告或 RAG。
2. 链路必须是 `CLI -> localhost:19825 daemon -> Browser Bridge -> Edge/Chrome`；只安装扩展不能替代 CLI/daemon。
3. `3jpz9ujj` 是扩展 `contextId`，必须在 daemon 在线后通过 `opencli profile list` 验证，不能把它当 Edge 文件夹名。
4. 小红书优先走内置 adapter；不要直接调用已失效或不稳定的内部搜索 XHR，不要手工拼接裸 note ID URL。
5. `search` 只做极小样本验证；详情、评论、下载必须复用搜索结果中带 `xsec_token` 的原始 URL。
6. 小红书内部 endpoint 被仓库标为 `internal-unstable`，必须保存原始页面证据、适配器版本、采集时间和失败状态。
7. OpenCLI 内部只有单次详情读取的有限冷却重试；项目外层仍需实现全局队列、并发 1、任务级令牌桶、熔断和审计。
8. 遇到安全限制、验证码、300017、300031、403 或登录墙立即停止，不循环重试。

正确的下一条执行路径：

```text
恢复 npm 依赖 -> npm run build -> 启动 CLI/daemon
-> opencli doctor -> opencli profile list
-> 确认 3jpz9ujj connected
-> xiaohongshu search "AI眼镜" --limit 1 -f json
-> 保存搜索 JSON 和完整签名 URL -> 只读 note 验证一次
```

在 `profile list` 显示 connected 之前，不执行小红书采集，也不开发后续数据库/RAG 主链路。

## 11. 动态执行结果（2026-09-09 第二轮）

在 §10 门槛满足后执行（doctor：daemon 19825 / 扩展 v1.0.24 / profile `3jpz9ujj` connected）。

环境解除：

1. `npm ci --ignore-scripts`：成功，232 包。§3 的 `ENOTCACHED` 为瞬时网络限制，registry 恢复后已通过。
2. `npm run build`：成功，生成 `dist/src/main.js`，cli-manifest 1366 条。
3. `node dist/src/main.js --version` = 1.8.8；`list` 1750 行；`xiaohongshu --help` 25 个子命令全部注册。

实测（授权测试账号，logged_in=true）：

| 命令 | 输入 | 结果 | 关键返回字段 |
|---|---|---|---|
| `whoami` | - | PASS | logged_in / username / followers |
| `feed` | `--limit 2` | PASS | id / title / type（video\|normal）/ author / likes / url（带 xsec_token） |
| `note` | 图文 note URL | PASS | author / content / likes / collects / comments；**title 为空**（字段缺失样本） |
| `note` | 视频 note URL | PASS | title / author / content（标签串）/ likes / collects / comments / tags |
| `comments` | `--limit 2` | PASS | rank / author / userId / profileUrl / text / likes / time / is_reply |

结论性证据：

- 图文与视频类型可通过 `type` / `note_type` 区分。
- 互动快照（likes / collects / comments）、作者、评论、时间均可结构化获取；tags 为逗号分隔串。
- 字段完整性依赖页面结构：图文 note 的 `title` 为空而视频正常，外层需显式标记缺失字段（手册 §4.5 关键字段完整率）。

失败路径（多轮测试捕获）：

1. **navigate 后断连**：`Browser connection dropped after the navigate command was dispatched`（code UNKNOWN，exitCode 1）。whoami 与 comments 首轮各出现一次，**重试即成功**。判定为瞬态；正式管道应在适配器外层加重试一次（不计入风控熔断）。
2. **search 稳定失败**：见 §12。

风控与限速：本轮为低频少量只读访问（约 10 次页面级请求 / 10 分钟内），未触发验证码、300017 / 300031、安全限制或 403，未做升压探测。`risk-control.js` 的冷却与熔断未被动触发；跨调用的全局限速仍需外层实现（手册 §4.4），与 §5 判断一致。

## 12. search 适配失效根因（2026-09-09）

两次执行均稳定复现：

```text
xiaohongshu search "AI眼镜" --limit 1 -f json
xiaohongshu search "AI眼镜" --limit 1 --sort latest --note-type image -f json
→ ok:false  code: COMMAND_EXEC
  "Xiaohongshu search filter layout did not match the expected visible panel (ambiguous_option)"
```

DOM 证据（`browser eval` 抓取当前搜索页）：`.search-layout__top > .filter` 内是折叠的“筛选”按钮 + SVG（textContent 为空，className 为 SVG 对象），`.filter-panel` 需点击后展开；面板结构已与 `clis/xiaohongshu/search.js` 的 `openPanel()` / `findOption()`（选择器 `.filters`、`.tag-container > .tags`，按文本匹配“综合 / 不限”等）假设不一致，同组文本歧义 → `ambiguous_option`。

判定：v1.8.8 的 search 适配器对该搜索页布局已过期。根因已定位并修复（见 §13），不影响 feed / note / comments 等已验证路径。

## 13. search 适配器修复结果（2026-09-09 第三轮）

### 根因（DOM 诊断确认）

`.filter-panel` 父子链（`.filter-panel < .filter < .search-layout__top`）与 5 个分组 label（排序依据/笔记类型/发布时间/搜索范围/位置距离）均正常。真正问题是**每个筛选选项在 DOM 中出现两次**：

- 无障碍隐藏副本：`aria-hidden="true"`、`opacity: 1e-05`、`z-index: -1`、无 `data-hp-bound`；
- 真实可见副本：无 `aria-hidden`、正常 opacity、有 `data-hp-bound`。

旧 `visible()` 只检查 `rect > 0 && display !== none && visibility !== hidden`，未排除 `aria-hidden` 与 `opacity≈0`，导致 `.tag-container > .tags` 对同一文本匹配到 2 个元素 → `ambiguous_option`。

### 修复（`clis/xiaohongshu/search.js`）

1. `visible()` 增加 `aria-hidden === 'true'` 与 `opacity < 0.05` 排除。
2. `resolveSearchFilters` 返回增加 `isDefault` 标记；`buildApplySearchFiltersJs` 序列化前剥离该字段。
3. `collectSearchHarvest`：全默认筛选时跳过 `requireFilterApplication`（默认值=不过滤，跳过不影响结果）。

### 实测

| 命令 | 结果 |
|---|---|
| `search "AI眼镜" --limit 1 -f json` | PASS，返回 title/author/likes/url(xsec_token)/published_at |
| `search "AI眼镜" --limit 2 --sort latest --note-type image -f json` | PASS，返回 2 条最新图文 |

遗留：部分显式筛选结果下 `likes` 返回“赞”占位文本（卡片渲染差异），属字段质量待优化，不阻塞主链路。
