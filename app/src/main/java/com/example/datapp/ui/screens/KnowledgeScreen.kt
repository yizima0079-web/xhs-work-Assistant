package com.example.datapp.ui.screens

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.Surface
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.example.datapp.data.ApiResult
import com.example.datapp.data.DatappRepository
import com.example.datapp.data.KbAnswer
import com.example.datapp.data.KbDocument
import com.example.datapp.data.KbQaPair
import com.example.datapp.data.KbSearchHit
import com.example.datapp.data.QaHistoryStore
import com.example.datapp.data.QaTurnRecord
import com.example.datapp.data.isUnauthorized
import com.example.datapp.ui.components.AttestRow
import com.example.datapp.ui.components.CollapsibleCard
import com.example.datapp.ui.components.EmptyCard
import com.example.datapp.ui.components.ErrorBanner
import com.example.datapp.ui.components.ExpandableText
import com.example.datapp.ui.components.GlassCard
import com.example.datapp.ui.components.LoadingRow
import com.example.datapp.ui.components.RichText
import com.example.datapp.ui.components.ScreenHeader
import com.example.datapp.ui.components.StatusChip
import com.example.datapp.ui.components.kbStatusColor
import com.example.datapp.ui.theme.DatappBlueDeep
import com.example.datapp.ui.theme.DatappInk
import com.example.datapp.ui.theme.DatappMuted
import com.example.datapp.ui.theme.DatappSurfaceSoft
import com.example.datapp.ui.theme.StatusApproved
import com.example.datapp.ui.theme.StatusPending
import com.example.datapp.ui.theme.StatusRejected
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.UUID

/**
 * 知识库 tab：问答 / 检索 / 文档 三模式。
 *
 * 前两个是纯读（服务端把 `POST /kb/search`、`POST /kb/ask` 列进了只读白名单）；
 * 「文档」里的向量化、删除、Q&A 通过/驳回是写操作，需要管理员会话。
 *
 * 问答的**拒答是正常结果**，不是错误 —— 服务端在无有效引用时会丢弃模型文本并返回
 * `answered=false`，这里必须如实呈现，绝不把它包装成"暂无数据"或补一段兜底答案。
 */
@Composable
fun KnowledgeScreen(
    repository: DatappRepository,
    documents: List<KbDocument>,
    documentsLoading: Boolean,
    documentsError: String?,
    onRefreshDocuments: suspend () -> Unit,
    accentColor: Color,
    bgColor: Color,
    onSessionExpired: () -> Unit,
) {
    val api = repository.apiClient
    val scope = rememberCoroutineScope()
    val context = LocalContext.current

    // 历史是**本地**资产（与 Web 看板 localStorage 同构），不依赖会话是否有效：
    // 后端连不上时，之前问过的内容照样能翻出来看。
    val historyStore = remember { QaHistoryStore(context) }
    var memoryEnabled by rememberSaveable { mutableStateOf(historyStore.memoryEnabled) }
    var history by remember { mutableStateOf(historyStore.load()) }

    var mode by rememberSaveable { mutableStateOf("问答") }

    var askQuery by rememberSaveable { mutableStateOf("") }
    var turns by remember { mutableStateOf(emptyList<Pair<String, KbAnswer>>()) }
    var askError by remember { mutableStateOf<String?>(null) }
    var asking by remember { mutableStateOf(false) }

    var searchQuery by rememberSaveable { mutableStateOf("") }
    var hits by remember { mutableStateOf(emptyList<KbSearchHit>()) }
    var searchError by remember { mutableStateOf<String?>(null) }
    var searching by remember { mutableStateOf(false) }

    var expandedDoc by remember { mutableStateOf<String?>(null) }
    var qaPairs by remember { mutableStateOf(emptyMap<String, List<KbQaPair>>()) }
    var qaError by remember { mutableStateOf<String?>(null) }
    var busyDoc by remember { mutableStateOf<String?>(null) }

    fun ask() {
        val q = askQuery.trim()
        if (q.isBlank() || asking) return
        askError = null
        asking = true
        scope.launch {
            // 记忆开启 → 带上最近 6 轮上下文；关闭 → 传空数组。
            // 「关掉记忆」必须是**真的不带上文**，不能只藏 UI 而照旧把历史发出去。
            val contextTurns =
                if (memoryEnabled) history.takeLast(QaHistoryStore.MAX_CONTEXT_TURNS) else emptyList()
            val payload = JSONArray().apply {
                contextTurns.forEach { rec ->
                    put(
                        JSONObject().apply {
                            put("query", rec.query)
                            put("answer", rec.answer)
                        }
                    )
                }
            }
            when (val r = withContext(Dispatchers.IO) { api.kbAsk(q, history = payload) }) {
                is ApiResult.Success -> {
                    val answer = KbAnswer.from(r.data)
                    // 6 轮是**请求上下文**的上限，不是能回看的条数上限 —— 展示不再跟着截到 6
                    turns = (turns + (q to answer)).takeLast(QaHistoryStore.MAX_TURNS)
                    val record = QaTurnRecord(
                        id = UUID.randomUUID().toString(),
                        query = q,
                        // 拒答轮次没有正文，存拒答原因 —— 与 Web 看板同一处理，
                        // 「问过但没依据」本身就是要留下的记录
                        answer = if (answer.answered) {
                            answer.answer
                        } else {
                            answer.limitations.joinToString("；").ifBlank { "本次未找到可核验答案" }
                        },
                        answered = answer.answered,
                        createdAt = isoNow(),
                    )
                    history = (history + record).takeLast(QaHistoryStore.MAX_TURNS)
                    historyStore.save(history)
                    askQuery = ""
                }
                is ApiResult.Error -> {
                    if (r.isUnauthorized) onSessionExpired() else askError = r.message
                }
            }
            asking = false
        }
    }

    /** 清空历史。当前会话结果一并清掉 —— 留着会让人以为历史没清干净。 */
    fun clearHistory() {
        history = emptyList()
        turns = emptyList()
        historyStore.clear()
    }

    fun search() {
        val q = searchQuery.trim()
        if (q.isBlank() || searching) return
        searchError = null
        searching = true
        scope.launch {
            when (val r = withContext(Dispatchers.IO) { api.kbSearch(q, 10) }) {
                is ApiResult.Success -> {
                    val list = mutableListOf<KbSearchHit>()
                    for (i in 0 until r.data.length()) {
                        r.data.optJSONObject(i)?.let { list.add(KbSearchHit.from(it)) }
                    }
                    hits = list
                }
                is ApiResult.Error -> {
                    if (r.isUnauthorized) onSessionExpired() else searchError = r.message
                }
            }
            searching = false
        }
    }

    fun loadQaPairs(docId: String) {
        scope.launch {
            qaError = null
            val (list, err) = withContext(Dispatchers.IO) { repository.fetchQaPairs(docId) }
            if (err != null) qaError = err else qaPairs = qaPairs + (docId to list)
        }
    }

    fun writeDoc(docId: String, label: String, call: suspend () -> ApiResult<*>) {
        if (busyDoc != null) return
        busyDoc = docId
        scope.launch {
            val r = withContext(Dispatchers.IO) { call() }
            busyDoc = null
            when {
                r is ApiResult.Error && r.isUnauthorized -> onSessionExpired()
                r is ApiResult.Error -> qaError = "$label 失败：${r.message}"
                else -> {
                    qaError = null
                    withContext(Dispatchers.IO) { onRefreshDocuments() }
                    if (expandedDoc == docId && label != "删除") loadQaPairs(docId)
                }
            }
        }
    }

    Box(Modifier.fillMaxSize().background(bgColor)) {
        LazyColumn(
            modifier = Modifier.fillMaxSize(),
            contentPadding = PaddingValues(start = 20.dp, end = 20.dp, top = 20.dp, bottom = 96.dp),
            verticalArrangement = Arrangement.spacedBy(14.dp),
        ) {
            item {
                Column {
                    ScreenHeader(
                        title = "知识库",
                        subtitle = "已审核内容向量化后的语义检索与带引用问答",
                        accentColor = accentColor,
                    )
                    Spacer(Modifier.height(14.dp))
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        listOf("问答", "检索", "文档").forEach { name ->
                            val selected = mode == name
                            Surface(
                                modifier = Modifier.clickable { mode = name },
                                shape = RoundedCornerShape(12.dp),
                                color = if (selected) accentColor else Color.White.copy(alpha = 0.8f),
                                border = BorderStroke(1.dp, if (selected) accentColor else Color.White),
                            ) {
                                Text(
                                    text = name,
                                    color = if (selected) Color.White else DatappMuted,
                                    fontSize = 13.sp,
                                    fontWeight = FontWeight.SemiBold,
                                    modifier = Modifier.padding(horizontal = 22.dp, vertical = 9.dp),
                                )
                            }
                        }
                    }
                }
            }

            // ------------------------------------------------ 问答
            if (mode == "问答") {
                item {
                    GlassCard(shape = RoundedCornerShape(18.dp)) {
                        Text("知识问答", style = MaterialTheme.typography.titleMedium)
                        Spacer(Modifier.height(4.dp))
                        Text(
                            "检索结果是唯一事实来源；没有有效引用时服务端会直接拒答",
                            color = DatappMuted,
                            fontSize = 11.5.sp,
                        )
                        Spacer(Modifier.height(10.dp))
                        OutlinedTextField(
                            value = askQuery,
                            onValueChange = { askQuery = it },
                            modifier = Modifier.fillMaxWidth(),
                            shape = RoundedCornerShape(14.dp),
                            placeholder = { Text("例如：什么样的开头钩子更容易出爆款？", color = DatappMuted, fontSize = 12.sp) },
                            maxLines = 3,
                            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Text, imeAction = ImeAction.Send),
                            colors = OutlinedTextFieldDefaults.colors(
                                focusedContainerColor = Color.White,
                                unfocusedContainerColor = Color.White.copy(alpha = 0.85f),
                                focusedBorderColor = accentColor.copy(alpha = 0.6f),
                                unfocusedBorderColor = Color.White,
                            ),
                        )
                        Spacer(Modifier.height(10.dp))
                        Button(
                            onClick = { ask() },
                            modifier = Modifier.fillMaxWidth().height(44.dp),
                            enabled = !asking && askQuery.isNotBlank(),
                            shape = RoundedCornerShape(13.dp),
                            colors = ButtonDefaults.buttonColors(containerColor = accentColor),
                        ) {
                            Text(if (asking) "检索并作答中…" else "提问", color = Color.White, fontSize = 13.sp, fontWeight = FontWeight.Bold)
                        }

                        Spacer(Modifier.height(6.dp))
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            Column(Modifier.weight(1f)) {
                                Text(
                                    "记住本地会话上下文",
                                    color = DatappInk,
                                    fontSize = 12.sp,
                                    fontWeight = FontWeight.SemiBold,
                                )
                                Spacer(Modifier.height(2.dp))
                                Text(
                                    if (history.isEmpty()) {
                                        "暂无历史对话"
                                    } else {
                                        "已保存 ${history.size} 轮 · 提问时带上最近 " +
                                            "${minOf(history.size, QaHistoryStore.MAX_CONTEXT_TURNS)} 轮"
                                    },
                                    color = DatappMuted,
                                    fontSize = 10.5.sp,
                                )
                            }
                            Switch(
                                checked = memoryEnabled,
                                onCheckedChange = {
                                    memoryEnabled = it
                                    historyStore.memoryEnabled = it
                                },
                            )
                        }

                        if (history.isNotEmpty()) {
                            TextButton(onClick = { clearHistory() }, contentPadding = PaddingValues(0.dp)) {
                                Text("清空历史", color = DatappMuted, fontSize = 11.sp, fontWeight = FontWeight.SemiBold)
                            }
                        }
                    }
                }

                if (askError != null) item { ErrorBanner(askError!!) }

                if (history.isNotEmpty()) {
                    item {
                        GlassCard(shape = RoundedCornerShape(18.dp)) {
                            Row(verticalAlignment = Alignment.CenterVertically) {
                                Text(
                                    "历史对话",
                                    color = DatappInk,
                                    fontSize = 12.5.sp,
                                    fontWeight = FontWeight.SemiBold,
                                    modifier = Modifier.weight(1f),
                                )
                                Text(
                                    "共 ${history.size} 轮 · 点一条回填问题",
                                    color = DatappMuted,
                                    fontSize = 10.5.sp,
                                )
                            }
                            Spacer(Modifier.height(2.dp))
                            history.takeLast(HISTORY_PREVIEW).reversed().forEach { record ->
                                HistoryRow(record, accentColor) { askQuery = record.query }
                            }
                        }
                    }
                }

                if (turns.isEmpty() && !asking) {
                    item { EmptyCard("还没有提问", "问点具体的，答案会带上知识库引用") }
                }

                itemsIndexed(
                    items = turns.reversed(),
                    key = { index, item -> "turn-${item.second.hashCode()}_$index" },
                ) { _, turn ->
                    AnswerCard(question = turn.first, answer = turn.second, accentColor = accentColor)
                }
            }

            // ------------------------------------------------ 检索
            if (mode == "检索") {
                item {
                    GlassCard(shape = RoundedCornerShape(18.dp)) {
                        Text("语义检索", style = MaterialTheme.typography.titleMedium)
                        Spacer(Modifier.height(4.dp))
                        Text(
                            "只命中已审核并向量化入库的知识片段",
                            color = DatappMuted,
                            fontSize = 11.5.sp,
                        )
                        Spacer(Modifier.height(10.dp))
                        OutlinedTextField(
                            value = searchQuery,
                            onValueChange = { searchQuery = it },
                            modifier = Modifier.fillMaxWidth(),
                            singleLine = true,
                            shape = RoundedCornerShape(14.dp),
                            placeholder = { Text("输入查询语句", color = DatappMuted, fontSize = 12.sp) },
                            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Text, imeAction = ImeAction.Search),
                            colors = OutlinedTextFieldDefaults.colors(
                                focusedContainerColor = Color.White,
                                unfocusedContainerColor = Color.White.copy(alpha = 0.85f),
                                focusedBorderColor = accentColor.copy(alpha = 0.6f),
                                unfocusedBorderColor = Color.White,
                            ),
                        )
                        Spacer(Modifier.height(10.dp))
                        Button(
                            onClick = { search() },
                            modifier = Modifier.fillMaxWidth().height(44.dp),
                            enabled = !searching && searchQuery.isNotBlank(),
                            shape = RoundedCornerShape(13.dp),
                            colors = ButtonDefaults.buttonColors(containerColor = accentColor),
                        ) {
                            Text(if (searching) "检索中…" else "检索", color = Color.White, fontSize = 13.sp, fontWeight = FontWeight.Bold)
                        }
                    }
                }

                if (searchError != null) item { ErrorBanner(searchError!!) }

                if (hits.isEmpty() && !searching) {
                    item { EmptyCard("暂无检索结果", "换个说法，或先到「文档」里把内容向量化") }
                }

                itemsIndexed(
                    items = hits,
                    key = { index, item -> "hit-${item.chunkId}_$index" },
                ) { _, hit ->
                    GlassCard(shape = RoundedCornerShape(16.dp)) {
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            Text(
                                hit.title,
                                color = DatappInk,
                                fontSize = 12.5.sp,
                                fontWeight = FontWeight.SemiBold,
                                modifier = Modifier.weight(1f),
                                maxLines = 1,
                                overflow = TextOverflow.Ellipsis,
                            )
                            StatusChip("${hit.scorePercent}%", accentColor)
                        }
                        Spacer(Modifier.height(7.dp))
                        // 命中原文是证据链本身，不能砍，但也不能铺满屏 —— 收 3 行、点开看全。
                        // 原先下面还有一行 `chunk <id>`：纯机码，对读结论的人零信息量，删。
                        ExpandableText(hit.text, Color(0xFF55667A), accentColor)
                    }
                }
            }

            // ------------------------------------------------ 文档
            if (mode == "文档") {
                if (documentsError != null) item { ErrorBanner(documentsError!!) }
                if (qaError != null) item { ErrorBanner(qaError!!) }

                item {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Text(
                            "文档（${documents.size}）",
                            style = MaterialTheme.typography.titleMedium,
                            modifier = Modifier.weight(1f),
                        )
                        TextButton(
                            onClick = { scope.launch { withContext(Dispatchers.IO) { onRefreshDocuments() } } },
                            contentPadding = PaddingValues(0.dp),
                        ) {
                            Text("刷新", color = accentColor, fontSize = 12.sp, fontWeight = FontWeight.SemiBold)
                        }
                    }
                }

                if (documentsLoading && documents.isEmpty()) {
                    item { LoadingRow("正在同步知识库文档…", accentColor) }
                } else if (documents.isEmpty()) {
                    item { EmptyCard("知识库为空", "到内容详情里把已审核的分析或内容存进来") }
                }

                itemsIndexed(
                    items = documents,
                    key = { index, item -> "doc-${item.docId}_$index" },
                ) { _, doc ->
                    DocumentCard(
                        doc = doc,
                        accentColor = accentColor,
                        expanded = expandedDoc == doc.docId,
                        busy = busyDoc == doc.docId,
                        pairs = qaPairs[doc.docId],
                        onToggle = {
                            expandedDoc = if (expandedDoc == doc.docId) null else doc.docId
                            if (expandedDoc == doc.docId) loadQaPairs(doc.docId)
                        },
                        onVectorize = {
                            writeDoc(doc.docId, "向量化") { api.vectorizeDocument(doc.docId) }
                        },
                        onDelete = {
                            writeDoc(doc.docId, "删除") { api.deleteKbDocument(doc.docId) }
                        },
                        onApprove = { ids ->
                            writeDoc(doc.docId, "通过问答对") { api.approveQaPairs(ids) }
                        },
                        onReject = { ids ->
                            writeDoc(doc.docId, "驳回问答对") { api.rejectQaPairs(ids) }
                        },
                    )
                }
            }
        }
    }
}

// ---------------------------------------------------------------- 问答结果

/** 历史列表只铺最近这么多条，与 Web 看板一致 —— 再多就把当前问答挤出首屏 */
private const val HISTORY_PREVIEW = 5

/**
 * 一条历史记录：状态 + 问题 + 答案摘要，**点一下只回填问题**。
 *
 * 刻意不重放答案：历史里没有那一轮的 citations / top_score（引用脱离当次检索快照就
 * 无法复现），重放会给出一个看起来有据、实际查无对应的答案 —— 比不给更坏。
 * 想再看结论就重新问一次，让证据链重新走一遍检索。
 */
@Composable
private fun HistoryRow(record: QaTurnRecord, accentColor: Color, onPick: () -> Unit) {
    Column(
        Modifier
            .fillMaxWidth()
            .clickable { onPick() }
            .padding(vertical = 7.dp),
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            StatusChip(
                text = if (record.answered) "已回答" else "未命中",
                color = if (record.answered) StatusApproved else StatusPending,
            )
            Spacer(Modifier.width(8.dp))
            Text(
                record.query,
                color = DatappInk,
                fontSize = 12.sp,
                fontWeight = FontWeight.SemiBold,
                modifier = Modifier.weight(1f),
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
            )
            Spacer(Modifier.width(6.dp))
            Text(formatHistoryTime(record.createdAt), color = DatappMuted, fontSize = 10.sp)
        }
        if (record.answer.isNotBlank()) {
            Spacer(Modifier.height(3.dp))
            Text(
                record.answer,
                color = DatappMuted,
                fontSize = 11.sp,
                lineHeight = 16.sp,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
            )
        }
    }
}

/** 存的是本地时区的 `yyyy-MM-dd'T'HH:mm:ss`，解析不出来就当空串，不抛异常打断列表 */
private fun formatHistoryTime(iso: String): String = try {
    SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss", Locale.US).parse(iso)
        ?.let { SimpleDateFormat("MM-dd HH:mm", Locale.US).format(it) }
        .orEmpty()
} catch (e: Exception) {
    ""
}

private fun isoNow(): String = SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss", Locale.US).format(Date())

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun AnswerCard(question: String, answer: KbAnswer, accentColor: Color) {
    GlassCard(
        shape = RoundedCornerShape(20.dp),
    ) {
        Text("Q  $question", color = DatappInk, fontSize = 13.5.sp, fontWeight = FontWeight.Bold, lineHeight = 20.sp)
        Spacer(Modifier.height(10.dp))

        if (!answer.answered) {
            // 拒答 = 服务端的红线行为，如实呈现，不补任何兜底文案
            Surface(
                shape = RoundedCornerShape(12.dp),
                color = StatusPending.copy(alpha = 0.1f),
            ) {
                Column(Modifier.padding(horizontal = 12.dp, vertical = 10.dp)) {
                    Text("无据拒答", color = StatusPending, fontSize = 12.sp, fontWeight = FontWeight.Bold)
                    Spacer(Modifier.height(4.dp))
                    Text(
                        answer.reasonLabel.ifBlank { "没有可支撑回答的有效引用" },
                        color = Color(0xFF6B5A3A),
                        fontSize = 11.5.sp,
                        lineHeight = 17.sp,
                    )
                    if (answer.limitations.isNotEmpty()) {
                        Spacer(Modifier.height(6.dp))
                        answer.limitations.forEach { Text("· $it", color = DatappMuted, fontSize = 11.sp, lineHeight = 16.sp) }
                    }
                }
            }
            return@GlassCard
        }

        // 这是模型自由生成的正文（没有 payload 可替代），markdown 标记该被**渲染掉**而不是删掉：
        // RichText 把 `**加粗**` 变成真加粗、`- ` 变成圆点，但不认标题 —— 结果页不该长成文档。
        RichText(answer.answer, accentColor)

        if (answer.limitations.isNotEmpty()) {
            Spacer(Modifier.height(10.dp))
            Text("局限说明", color = DatappMuted, fontSize = 11.sp, fontWeight = FontWeight.SemiBold)
            answer.limitations.forEach { Text("· $it", color = DatappMuted, fontSize = 11.sp, lineHeight = 16.sp) }
        }

        if (answer.citations.isNotEmpty()) {
            Spacer(Modifier.height(12.dp))
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text("引用（${answer.citations.size}）", color = DatappMuted, fontSize = 11.sp, fontWeight = FontWeight.SemiBold)
                Spacer(Modifier.width(8.dp))
                Text("最高相似度 ${(answer.topScore * 100).toInt()}%", color = DatappMuted, fontSize = 10.5.sp)
            }
            Spacer(Modifier.height(6.dp))
            answer.citations.forEach { c ->
                Surface(
                    modifier = Modifier.fillMaxWidth().padding(top = 6.dp),
                    shape = RoundedCornerShape(12.dp),
                    color = DatappSurfaceSoft,
                ) {
                    Column(Modifier.padding(horizontal = 11.dp, vertical = 9.dp)) {
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            Text(
                                // 没标题就写「未命名来源」—— 原先回落成 doc_id，等于把机器码当标题给用户
                                c.title.ifBlank { "未命名来源" },
                                color = DatappInk,
                                fontSize = 11.5.sp,
                                fontWeight = FontWeight.SemiBold,
                                modifier = Modifier.weight(1f),
                                maxLines = 1,
                                overflow = TextOverflow.Ellipsis,
                            )
                            StatusChip(
                                text = if (c.verified) "已验证 ${c.scorePercent}%" else "${c.scorePercent}%",
                                color = if (c.verified) StatusApproved else DatappMuted,
                            )
                        }
                        Spacer(Modifier.height(5.dp))
                        ExpandableText(c.text, Color(0xFF55667A), accentColor)
                    }
                }
            }
        }
    }
}

// ---------------------------------------------------------------- 文档卡片

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun DocumentCard(
    doc: KbDocument,
    accentColor: Color,
    expanded: Boolean,
    busy: Boolean,
    pairs: List<KbQaPair>?,
    onToggle: () -> Unit,
    onVectorize: () -> Unit,
    onDelete: () -> Unit,
    onApprove: (List<String>) -> Unit,
    onReject: (List<String>) -> Unit,
) {
    GlassCard(shape = RoundedCornerShape(18.dp)) {
        Row(
            modifier = Modifier.fillMaxWidth().clickable(onClick = onToggle),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Column(Modifier.weight(1f)) {
                Text(
                    doc.title,
                    color = DatappInk,
                    fontSize = 13.5.sp,
                    fontWeight = FontWeight.Bold,
                    maxLines = 2,
                    overflow = TextOverflow.Ellipsis,
                )
                Spacer(Modifier.height(5.dp))
                FlowRow(
                    horizontalArrangement = Arrangement.spacedBy(6.dp),
                    verticalArrangement = Arrangement.spacedBy(6.dp),
                ) {
                    StatusChip(doc.statusLabel, kbStatusColor(doc.status))
                    StatusChip(doc.sourceLabel, accentColor)
                    if (doc.chunkCount > 0) StatusChip("${doc.chunkCount} chunks", DatappMuted)
                    if (doc.isQaDoc && doc.qaPairCount > 0) {
                        StatusChip("Q&A ${doc.qaApprovedCount}/${doc.qaPairCount}", StatusApproved)
                    }
                }
                Spacer(Modifier.height(5.dp))
                Text("${doc.author.ifBlank { "—" }} · ${doc.createdAt}", color = DatappMuted, fontSize = 10.5.sp)
            }
            Text(if (expanded) "⌃" else "⌄", color = DatappMuted, fontSize = 16.sp, fontWeight = FontWeight.Bold)
        }

        if (expanded) {
            Spacer(Modifier.height(12.dp))
            // doc_id 与来源 URL 只在回服务端查证时有用：前者是纯机码，后者在 app 上点不动。
            // 原先直接铺在卡片里，现在收进默认收起的存证卡。
            CollapsibleCard("数据存证") {
                AttestRow("文档 ID", doc.docId)
                AttestRow("来源", doc.url.ifBlank { "—" })
            }

            Spacer(Modifier.height(12.dp))
            Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                if (doc.canVectorize) {
                    Button(
                        onClick = onVectorize,
                        modifier = Modifier.weight(1f).height(40.dp),
                        enabled = !busy,
                        shape = RoundedCornerShape(12.dp),
                        colors = ButtonDefaults.buttonColors(containerColor = accentColor),
                    ) {
                        Text(if (busy) "处理中…" else "向量化", color = Color.White, fontSize = 12.sp, fontWeight = FontWeight.Bold)
                    }
                } else {
                    Surface(
                        modifier = Modifier.weight(1f).height(40.dp),
                        shape = RoundedCornerShape(12.dp),
                        color = StatusApproved.copy(alpha = 0.1f),
                        border = BorderStroke(1.dp, StatusApproved.copy(alpha = 0.3f)),
                    ) {
                        Box(contentAlignment = Alignment.Center) {
                            Text(
                                if (doc.status == "ready") "已可检索" else doc.statusLabel,
                                color = StatusApproved,
                                fontSize = 12.sp,
                                fontWeight = FontWeight.SemiBold,
                            )
                        }
                    }
                }
                Button(
                    onClick = onDelete,
                    modifier = Modifier.weight(1f).height(40.dp),
                    enabled = !busy,
                    shape = RoundedCornerShape(12.dp),
                    colors = ButtonDefaults.buttonColors(containerColor = Color.White),
                    border = BorderStroke(1.dp, StatusRejected.copy(alpha = 0.35f)),
                ) {
                    Text("删除", color = StatusRejected, fontSize = 12.sp, fontWeight = FontWeight.Bold)
                }
            }

            if (doc.isQaDoc) {
                Spacer(Modifier.height(14.dp))
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text("问答对", style = MaterialTheme.typography.titleSmall, modifier = Modifier.weight(1f))
                    if (pairs == null) {
                        Text("加载中…", color = DatappMuted, fontSize = 11.sp)
                    } else if (pairs.isNotEmpty()) {
                        val drafts = pairs.filter { it.isDraft }.map { it.qaId }
                        if (drafts.isNotEmpty()) {
                            TextButton(
                                onClick = { onApprove(drafts) },
                                contentPadding = PaddingValues(0.dp),
                            ) {
                                Text("全部通过", color = StatusApproved, fontSize = 11.5.sp, fontWeight = FontWeight.SemiBold)
                            }
                        }
                    }
                }
                pairs?.forEach { pair ->
                    Surface(
                        modifier = Modifier.fillMaxWidth().padding(top = 8.dp),
                        shape = RoundedCornerShape(12.dp),
                        color = DatappSurfaceSoft,
                    ) {
                        Column(Modifier.padding(11.dp)) {
                            Row(verticalAlignment = Alignment.CenterVertically) {
                                StatusChip(
                                    pair.statusLabel,
                                    when (pair.status) {
                                        "approved" -> StatusApproved
                                        "rejected" -> StatusRejected
                                        else -> StatusPending
                                    },
                                )
                                Spacer(Modifier.weight(1f))
                                if (pair.isDraft) {
                                    TextButton(onClick = { onApprove(listOf(pair.qaId)) }, contentPadding = PaddingValues(0.dp)) {
                                        Text("通过", color = StatusApproved, fontSize = 11.sp, fontWeight = FontWeight.SemiBold)
                                    }
                                    Spacer(Modifier.width(10.dp))
                                    TextButton(onClick = { onReject(listOf(pair.qaId)) }, contentPadding = PaddingValues(0.dp)) {
                                        Text("驳回", color = StatusRejected, fontSize = 11.sp, fontWeight = FontWeight.SemiBold)
                                    }
                                }
                            }
                            Spacer(Modifier.height(6.dp))
                            Text(pair.question, color = DatappInk, fontSize = 12.sp, fontWeight = FontWeight.SemiBold, lineHeight = 18.sp)
                            if (pair.answer.isNotBlank()) {
                                Spacer(Modifier.height(5.dp))
                                Text(pair.answer, color = Color(0xFF55667A), fontSize = 11.5.sp, lineHeight = 17.sp)
                            }
                            // 六维拆解：原先拍平成「· 标签：值」的文本行，看起来像目录；
                            // 每维一张小卡 —— 标签做 chip、值做正文，才撑得起「拆解」这个词。
                            pair.dimensions.forEach { (label, value) ->
                                if (value.isBlank()) return@forEach
                                Spacer(Modifier.height(7.dp))
                                Surface(
                                    modifier = Modifier.fillMaxWidth(),
                                    shape = RoundedCornerShape(10.dp),
                                    // 卡片里不再叠第二层白：白卡套白块只会读成「框里嵌了个白框」
                                    color = DatappSurfaceSoft,
                                ) {
                                    Column(Modifier.padding(horizontal = 10.dp, vertical = 8.dp)) {
                                        Text(
                                            label,
                                            color = accentColor,
                                            fontSize = 10.5.sp,
                                            fontWeight = FontWeight.SemiBold,
                                        )
                                        Spacer(Modifier.height(3.dp))
                                        Text(
                                            value,
                                            color = Color(0xFF55667A),
                                            fontSize = 11.5.sp,
                                            lineHeight = 17.sp,
                                        )
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}
