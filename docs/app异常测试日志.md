# DatApp Android 端异常与边界场景测试日志

创建时间：2026-09-10
测试类型：应用边界、并发/状态竞争、空值/类型转换容错、导航与系统手势

## 📋 异常与边界问题清单概览

| 编号 | 模块 / 场景 | 问题描述 | 风险等级 | 修复状态 |
| :--- | :--- | :--- | :--- | :--- |
| **ERR-01** | `DatappApp` 状态恢复 | `AppTab.valueOf(selectedTab)` 在未知字符串/状态反序列化异常时抛出 `IllegalArgumentException` 导致崩溃 | P0 (Crash) | [x] 已修复 |
| **ERR-02** | `Sparkline` 绘图组件 | 趋势折线图数据点 `<= 1` 时除以零产生 `NaN`/`Infinity` 坐标，引发 Canvas 绘图异常或崩溃 | P1 (Visual/Crash) | [x] 已修复 |
| **ERR-03** | `DatappApp` 状态持久化 | `favoriteReports` 使用 `remember` 而非 `rememberSaveable`，旋屏/Activity 重建后状态丢失；且缺乏并发切换保护 | P1 (State Loss) | [x] 已修复 |
| **ERR-04** | `DatappApp` 导航手势 | 详情页缺少 `BackHandler` 拦截，点击系统返回键/手势直接退出 App；切 Tab 时未清除详情状态可能引发死锁 | P0 (Navigation) | [x] 已修复 |
| **ERR-05** | `InsightsScreen` 搜索过滤 | 搜索输入未进行 `trim()` 处理，带空格查询无法匹配；搜索无结果时缺少空状态 UI 提示 | P2 (UX Boundary) | [x] 已修复 |
| **ERR-06** | `InsightCard` / `ReportCard` | 长文本或大字号模式下缺少 `maxLines` 与 `TextOverflow.Ellipsis` 限制，导致布局挤压变形 | P2 (Visual Overflow) | [x] 已修复 |
| **ERR-07** | `LazyColumn` Key 冲突 | 列表项 `key` 使用纯自增 ID，在高并发动态数据重排或存在重复数据时抛出 `IllegalArgumentException` 崩溃 | P1 (Crash) | [x] 已修复 |

---

## 🔍 详细问题描述与修复记录

### ERR-01: AppTab 反序列化崩溃 [x] 已修复
- **现象**：当 `rememberSaveable` 恢复的 `selectedTab` 包含无效名称（如旧版本版本迁移、DeepLink 注入或 Bundle 损坏）时，`AppTab.valueOf(...)` 直接抛出未捕获异常。
- **复现方式**：传入 `"INVALID_TAB"` 至 `selectedTab`。
- **修复方案**：使用 `AppTab.entries.find { it.name == selectedTab } ?: AppTab.Home` 进行安全查找与回退防御，防止反序列化未知 Tab 导致应用崩溃。
- **验证结果**：单元测试 `testIssue1_InvalidTabNameThrowsInUnsafe_AndHandledInSafe` 通过。

### ERR-02: Sparkline 折线图除零与 NaN 异常 [x] 已修复
- **现象**：当趋势数据点只有一个或为空时，`(values.size - 1)` 为 0，坐标计算公式 `index * width / 0` 得到 `NaN` 或 `Infinity`，传递给 `Canvas.drawLine/drawPath` 引起异常。
- **复现方式**：传入只有一个元素的 `values` 列表至 `Sparkline`。
- **修复方案**：对空列表及容器尺寸为 0 直接提前 return，对单元素居中渲染，并使用 `(values.size - 1).coerceAtLeast(1)` 规避除以零错误。
- **验证结果**：单元测试 `testIssue2_SparklineSingleItemDivisionByZero_HandledInSafe` 通过。

### ERR-03: 收藏状态旋屏丢失与高频点击竞争 [x] 已修复
- **现象**：`favoriteReports` 在 Activity 重建（如旋转屏幕、系统主题切换）后清空。高频连续点击收藏图标时，非原子状态修改容易产生并发状态不一致。
- **复现方式**：收藏某份报告后旋转屏幕，收藏状态变为未收藏；或快速双击收藏图标。
- **修复方案**：将 `favoriteReports` 升级为 `rememberSaveable { mutableStateOf(setOf<Int>()) }` 实现旋屏持久化，并重构为 Set 不可变集合代换机制。
- **验证结果**：单元测试 `testIssue3_ConcurrentFavoritesToggleRaceCondition` 通过。

### ERR-04: 系统返回手势与 Tab 导航死锁 [x] 已修复
- **现象**：在 `ReportDetail` 或 `InsightDetail` 页面时，按系统返回键直接退出应用，而不是返回上级列表；若在详情页切换 Tab，状态层级竞争可能导致无法切回列表。
- **复现方式**：进入报告详情页，点击系统返回键。
- **修复方案**：引入 `BackHandler(enabled = selectedReport != null || selectedInsight != null)` 拦截系统返回手势优先关闭详情页；并在 BottomBar Item 触发点击时统一将 `selectedReportId` 与 `selectedInsightId` 重置为 0。
- **验证结果**：状态导航重构完成，组件通过编译并具备正确的手势分发响应。

### ERR-05: 搜索框空格未裁剪与空状态缺失 [x] 已修复
- **现象**：输入带首尾空格的关键词（如 `" 生活 "`）搜索不到任何数据；搜无结果时仅展示一片空白，缺乏“未找到相关内容”的引导。
- **复现方式**：在洞察页搜索 `"  生活  "` 或随便输入一段无匹配字符。
- **修复方案**：在过滤函数中对查询串执行 `query.trim()`，并扩展搜索匹配至标题、摘要和数据来源；在搜索结果为空时渲染友好的空状态卡片 `Card`。
- **验证结果**：单元测试 `testIssue5_SearchQueryWhitespaceHandling` 通过。

### ERR-06: 文本溢出与大字号适配缺陷 [x] 已修复
- **现象**：无 `maxLines` 和 `Ellipsis` 保护，当文章标题极长或无空格字符，或者系统开启大字体模式时，卡片内部元素被挤出可见区域。
- **复现方式**：模拟非常长的标题或极大字号。
- **修复方案**：对 `InsightCard`、`RecentReportCard`、`MetricCard` 的标题、摘要与标签设置 `maxLines` 与 `overflow = TextOverflow.Ellipsis`。
- **验证结果**：编译通过，布局具备自适应省略截断能力。

### ERR-07: LazyColumn 列表 Key 重复引发崩溃 [x] 已修复
- **现象**：`LazyColumn` 的 `key` 使用了纯自增 `insight.id` / `report.id`，高并发数据生成或接口返回重复 ID 时触发 Compose `IllegalArgumentException` 崩溃。
- **复现方式**：列表传入两个相同 `id` 的 Data 对象。
- **修复方案**：将列表遍历切换为 `itemsIndexed`，使用带有索引前缀的组合键 `"${id}_$index"` 强制确保 Key 唯一性。
- **验证结果**：单元测试 `testIssue7_DuplicateDataKeySafetyCheck` 通过。
