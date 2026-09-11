package com.example.datapp.ui.screens

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import coil.compose.AsyncImage
import com.example.datapp.data.Analysis
import com.example.datapp.data.ApiResult
import com.example.datapp.data.ClaimSnapshot
import com.example.datapp.data.Content
import com.example.datapp.data.ContentReviewDetail
import com.example.datapp.data.DatappRepository
import com.example.datapp.data.ReportDetail
import com.example.datapp.data.errorMessage
import com.example.datapp.data.isUnauthorized
import com.example.datapp.ui.components.AttestRow
import com.example.datapp.ui.components.BackHeader
import com.example.datapp.ui.components.BulletList
import com.example.datapp.ui.components.ChipRow
import com.example.datapp.ui.components.CollapsibleCard
import com.example.datapp.ui.components.ConfidenceBar
import com.example.datapp.ui.components.EmptyCard
import com.example.datapp.ui.components.ErrorBanner
import com.example.datapp.ui.components.GlassCard
import com.example.datapp.ui.components.LoadingRow
import com.example.datapp.ui.components.StatusChip
import com.example.datapp.ui.components.claimStatusColor
import com.example.datapp.ui.components.reviewStatusColor
import com.example.datapp.ui.components.sourceKindLabel
import com.example.datapp.ui.theme.DatappBlueDeep
import com.example.datapp.ui.theme.DatappInk
import com.example.datapp.ui.theme.DatappMuted
import com.example.datapp.ui.theme.DatappSurfaceSoft
import com.example.datapp.ui.theme.StatusApproved
import com.example.datapp.ui.theme.StatusRejected
import com.example.datapp.ui.theme.StatusUncertain
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/**
 * 内容详情：分析 / 审核 / 报告 三面板。
 *
 * 对齐前端看板 ContentCard 的内嵌面板模式 —— 三种操作针对**同一条内容**，
 * 分散到三个页面会逼用户在 tab 之间来回跳，还会丢失当前内容的上下文。
 *
 * 写操作走设备令牌或管理员 Cookie（两者都被服务端接受，取值见 `applyAuth`），
 * 401 时提示重新登录，其余错误原样显示服务端返回的 detail。
 */
@Composable
fun ContentDetailScreen(
    content: Content,
    coverUrl: String?,
    repository: DatappRepository,
    accentColor: Color,
    onBack: () -> Unit,
    onOpenReport: (String) -> Unit,
    onSessionExpired: () -> Unit,
    onContentChanged: () -> Unit = {},
) {
    val api = repository.apiClient
    val scope = rememberCoroutineScope()

    var panel by remember { mutableStateOf("分析") }

    var analyses by remember { mutableStateOf(emptyList<Analysis>()) }
    var analysesLoading by remember { mutableStateOf(true) }
    var analysesError by remember { mutableStateOf<String?>(null) }
    var selectedAnalysisIndex by remember { mutableStateOf(0) }

    var review by remember { mutableStateOf<ContentReviewDetail?>(null) }
    var reviewLoading by remember { mutableStateOf(true) }
    var reviewError by remember { mutableStateOf<String?>(null) }
    var batchId by remember { mutableStateOf<String?>(null) }

    var reports by remember { mutableStateOf(emptyList<ReportDetail>()) }
    var reportsLoading by remember { mutableStateOf(true) }
    var reportsError by remember { mutableStateOf<String?>(null) }

    var busy by remember { mutableStateOf<String?>(null) }
    var notice by remember { mutableStateOf<String?>(null) }
    var noticeOk by remember { mutableStateOf(true) }
    var focus by remember { mutableStateOf("") }
    // 全项目第一个删除确认弹窗：这里删的是源内容，误触虽然可恢复，
    // 但必须让用户先知道「可恢复」这件事，否则会以为数据没了
    var confirmHide by remember { mutableStateOf(false) }

    suspend fun loadAnalyses() {
        analysesLoading = true
        val (list, err) = withContext(Dispatchers.IO) { repository.fetchAnalyses(content.contentId) }
        analyses = list
        analysesError = err
        selectedAnalysisIndex = 0
        analysesLoading = false
    }

    suspend fun loadReview() {
        reviewLoading = true
        val (detail, err) = withContext(Dispatchers.IO) {
            repository.fetchReviewDetail(content.contentId, batchId)
        }
        review = detail
        reviewError = err
        reviewLoading = false
    }

    suspend fun loadReports() {
        reportsLoading = true
        val (list, err) = withContext(Dispatchers.IO) {
            repository.fetchReports(50, content.contentId)
        }
        reports = list
        reportsError = err
        reportsLoading = false
    }

    LaunchedEffect(content.contentId) {
        loadAnalyses()
        loadReview()
        loadReports()
    }

    LaunchedEffect(batchId) {
        if (review != null || batchId != null) loadReview()
    }

    /** 统一的写操作执行器：负责 busy 态、401 兜底、成功/失败提示与后续刷新 */
    fun action(label: String, after: suspend () -> Unit = {}, call: suspend () -> ApiResult<*>) {
        if (busy != null) return
        busy = label
        notice = null
        scope.launch {
            val result = withContext(Dispatchers.IO) { call() }
            busy = null
            if (result is ApiResult.Error && result.isUnauthorized) {
                onSessionExpired()
                return@launch
            }
            if (result is ApiResult.Error) {
                noticeOk = false
                notice = result.message
            } else {
                noticeOk = true
                notice = "$label 完成"
                after()
            }
        }
    }

    LazyColumn(
        modifier = Modifier.fillMaxSize(),
        contentPadding = PaddingValues(start = 20.dp, end = 20.dp, top = 18.dp, bottom = 96.dp),
        verticalArrangement = Arrangement.spacedBy(16.dp),
    ) {
        item {
            BackHeader(
                title = "内容详情",
                accentColor = accentColor,
                onBack = onBack,
                trailing = {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        StatusChip(content.reviewStatusLabel, reviewStatusColor(content.reviewStatus))
                        Spacer(Modifier.width(8.dp))
                        // 「删除」= 从 app 端移除。放这里而不是各面板里：它是针对
                        // **这条内容**的动作，不属于分析/审核/报告任何一面
                        Surface(
                            onClick = { confirmHide = true },
                            enabled = busy == null,
                            shape = RoundedCornerShape(10.dp),
                            color = StatusRejected.copy(alpha = 0.12f),
                            border = BorderStroke(1.dp, StatusRejected.copy(alpha = 0.35f)),
                        ) {
                            Text(
                                text = "删除",
                                color = StatusRejected,
                                fontSize = 12.sp,
                                fontWeight = FontWeight.SemiBold,
                                modifier = Modifier.padding(horizontal = 13.dp, vertical = 6.dp),
                            )
                        }
                    }
                },
            )
        }

        // ---- 作品本体 ----
        // 内容 ID 挪进下方的「数据存证」：它只在回服务端查证时有用，
        // 摆在作品卡里就是一行没人看的机器码。
        item {
            GlassCard(shape = RoundedCornerShape(22.dp)) {
                if (!coverUrl.isNullOrBlank()) {
                    Box(
                        modifier = Modifier
                            .fillMaxWidth()
                            .height(180.dp)
                            .clip(RoundedCornerShape(16.dp))
                            .background(DatappSurfaceSoft),
                    ) {
                        AsyncImage(
                            model = coverUrl,
                            contentDescription = content.title,
                            modifier = Modifier.fillMaxSize(),
                            contentScale = ContentScale.Crop,
                        )
                        Surface(
                            modifier = Modifier.align(Alignment.TopEnd).padding(10.dp),
                            shape = RoundedCornerShape(8.dp),
                            color = Color.Black.copy(alpha = 0.65f),
                        ) {
                            Text(
                                content.platform.uppercase(),
                                color = Color.White,
                                fontSize = 10.sp,
                                fontWeight = FontWeight.Bold,
                                modifier = Modifier.padding(horizontal = 8.dp, vertical = 3.dp),
                            )
                        }
                    }
                    Spacer(Modifier.height(12.dp))
                }

                Text(content.title, color = DatappInk, fontSize = 16.sp, fontWeight = FontWeight.Bold, lineHeight = 22.sp)
                Spacer(Modifier.height(6.dp))
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text("作者: ${content.authorName}", color = DatappInk, fontSize = 12.sp, fontWeight = FontWeight.SemiBold)
                    Spacer(Modifier.width(10.dp))
                    Text("发布: ${content.publishedAt}", color = DatappMuted, fontSize = 12.sp)
                }
                Spacer(Modifier.height(8.dp))
                Text(
                    "♥ ${content.likes}   💬 ${content.comments}   ⤴ ${content.shares}   ☆ ${content.collects}",
                    color = DatappBlueDeep,
                    fontSize = 12.sp,
                    fontWeight = FontWeight.SemiBold,
                )
                if (content.text.isNotBlank()) {
                    Spacer(Modifier.height(10.dp))
                    Text(
                        content.text,
                        color = Color(0xFF55667A),
                        fontSize = 12.5.sp,
                        lineHeight = 19.sp,
                        maxLines = 6,
                        overflow = TextOverflow.Ellipsis,
                    )
                }
            }
        }

        item {
            CollapsibleCard("数据存证") {
                AttestRow("内容 ID", content.contentId)
                AttestRow("平台", content.platform)
            }
        }

        // ---- 面板切换 ----
        item {
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                listOf("分析", "审核", "报告").forEach { name ->
                    val selected = panel == name
                    Surface(
                        onClick = { panel = name },
                        shape = RoundedCornerShape(12.dp),
                        color = if (selected) accentColor else Color.White.copy(alpha = 0.8f),
                        border = BorderStroke(1.dp, if (selected) accentColor else Color.White),
                    ) {
                        Text(
                            text = name,
                            color = if (selected) Color.White else DatappMuted,
                            fontSize = 13.sp,
                            fontWeight = FontWeight.SemiBold,
                            modifier = Modifier.padding(horizontal = 20.dp, vertical = 9.dp),
                        )
                    }
                }
            }
        }

        if (notice != null) {
            item {
                NoticeBanner(text = notice!!, ok = noticeOk, accentColor = accentColor)
            }
        }

        if (busy != null) {
            item { LoadingRow("$busy 中…（大模型推理可能需要 1–3 分钟）", accentColor) }
        }

        // ---- 分析面板 ----
        if (panel == "分析") {
            if (analysesError != null) item { ErrorBanner(analysesError!!) }

            item {
                GlassCard(shape = RoundedCornerShape(18.dp)) {
                    Text("触发分析", style = MaterialTheme.typography.titleMedium)
                    Spacer(Modifier.height(4.dp))
                    Text(
                        "新增一版分析结论，历史版本保留可回溯",
                        color = DatappMuted,
                        fontSize = 11.5.sp,
                    )
                    Spacer(Modifier.height(10.dp))
                    OutlinedTextField(
                        value = focus,
                        onValueChange = { focus = it },
                        modifier = Modifier.fillMaxWidth(),
                        singleLine = true,
                        shape = RoundedCornerShape(14.dp),
                        placeholder = { Text("可选的关注点，如「开头钩子」", color = DatappMuted, fontSize = 12.sp) },
                        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Text),
                        colors = OutlinedTextFieldDefaults.colors(
                            focusedContainerColor = Color.White,
                            unfocusedContainerColor = Color.White.copy(alpha = 0.85f),
                            focusedBorderColor = accentColor.copy(alpha = 0.6f),
                            unfocusedBorderColor = Color.White,
                        ),
                    )
                    Spacer(Modifier.height(12.dp))
                    Button(
                        onClick = {
                            action("触发分析", after = { loadAnalyses() }) {
                                api.analyzeContent(content.contentId, focus.trim())
                            }
                        },
                        modifier = Modifier.fillMaxWidth().height(44.dp),
                        enabled = busy == null,
                        shape = RoundedCornerShape(13.dp),
                        colors = ButtonDefaults.buttonColors(containerColor = accentColor),
                    ) {
                        Text("开始分析", color = Color.White, fontSize = 13.sp, fontWeight = FontWeight.Bold)
                    }
                }
            }

            if (analysesLoading && analyses.isEmpty()) {
                item { LoadingRow("正在读取分析版本…", accentColor) }
            } else if (analyses.isEmpty()) {
                item { EmptyCard("还没有分析结论", "点上面的「开始分析」跑第一版") }
            } else {
                item {
                    Column {
                        Text("分析版本（${analyses.size}）", style = MaterialTheme.typography.titleMedium)
                        Spacer(Modifier.height(8.dp))
                        Row(
                            Modifier.horizontalScroll(rememberScrollState()),
                            horizontalArrangement = Arrangement.spacedBy(8.dp),
                        ) {
                            analyses.forEachIndexed { index, item ->
                                val selected = index == selectedAnalysisIndex
                                Surface(
                                    onClick = { selectedAnalysisIndex = index },
                                    shape = RoundedCornerShape(10.dp),
                                    color = if (selected) accentColor.copy(alpha = 0.14f) else Color.White.copy(alpha = 0.75f),
                                    border = BorderStroke(1.dp, if (selected) accentColor.copy(alpha = 0.4f) else Color.White),
                                ) {
                                    Text(
                                        text = "v${analyses.size - index}",
                                        color = if (selected) accentColor else DatappMuted,
                                        fontSize = 11.5.sp,
                                        fontWeight = FontWeight.SemiBold,
                                        modifier = Modifier.padding(horizontal = 12.dp, vertical = 6.dp),
                                    )
                                }
                            }
                        }
                    }
                }

                val current = analyses.getOrNull(selectedAnalysisIndex)
                if (current != null) {
                    item { AnalysisPanel(current, accentColor) }
                    item {
                        CollapsibleCard("数据存证") {
                            AttestRow("分析 ID", current.analysisId)
                            AttestRow("内容 ID", current.contentId)
                            AttestRow("分析版本", current.analysisVersion.ifBlank { "—" })
                            AttestRow("生成时间", current.createdAt)
                            AttestRow("关注点", current.focus.ifBlank { "未指定" })
                            AttestRow(
                                "基线",
                                if (current.baselineCount > 0) "${current.baselineCount} 条" else "无有效基线",
                            )
                        }
                    }
                    item {
                        GlassCard(shape = RoundedCornerShape(18.dp)) {
                            Text("入库与蒸馏", style = MaterialTheme.typography.titleMedium)
                            Spacer(Modifier.height(4.dp))
                            Text(
                                "入库后该分析才可被检索；蒸馏 Q&A 会产出六维拆解知识原子",
                                color = DatappMuted,
                                fontSize = 11.5.sp,
                            )
                            Spacer(Modifier.height(12.dp))
                            Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                                Button(
                                    onClick = {
                                        action("保存分析到知识库") { api.saveAnalysisToKb(current.analysisId) }
                                    },
                                    modifier = Modifier.weight(1f).height(42.dp),
                                    enabled = busy == null,
                                    shape = RoundedCornerShape(12.dp),
                                    colors = ButtonDefaults.buttonColors(containerColor = accentColor),
                                ) {
                                    Text("保存到知识库", color = Color.White, fontSize = 12.sp, fontWeight = FontWeight.Bold)
                                }
                                Button(
                                    onClick = {
                                        action("蒸馏 Q&A") { api.distillAnalysisQa(current.analysisId, force = true) }
                                    },
                                    modifier = Modifier.weight(1f).height(42.dp),
                                    enabled = busy == null,
                                    shape = RoundedCornerShape(12.dp),
                                    colors = ButtonDefaults.buttonColors(containerColor = Color.White),
                                    border = BorderStroke(1.dp, accentColor.copy(alpha = 0.4f)),
                                ) {
                                    Text("蒸馏 Q&A", color = accentColor, fontSize = 12.sp, fontWeight = FontWeight.Bold)
                                }
                            }
                        }
                    }
                }
            }

            item {
                GlassCard(
                    shape = RoundedCornerShape(18.dp),
                ) {
                    Text("内容整体入库", style = MaterialTheme.typography.titleMedium)
                    Spacer(Modifier.height(4.dp))
                    Text(
                        "服务端有红线门禁：内容 review_status 必须为 approved 才放行，否则拒绝。",
                        color = DatappMuted,
                        fontSize = 11.5.sp,
                        lineHeight = 17.sp,
                    )
                    Spacer(Modifier.height(12.dp))
                    Button(
                        onClick = { action("内容入库") { api.saveContentToKb(content.contentId) } },
                        modifier = Modifier.fillMaxWidth().height(42.dp),
                        enabled = busy == null,
                        shape = RoundedCornerShape(12.dp),
                        colors = ButtonDefaults.buttonColors(containerColor = Color.White),
                        border = BorderStroke(1.dp, accentColor.copy(alpha = 0.4f)),
                    ) {
                        Text("把这条内容存入知识库", color = accentColor, fontSize = 12.sp, fontWeight = FontWeight.Bold)
                    }
                }
            }
        }

        // ---- 审核面板 ----
        if (panel == "审核") {
            if (reviewError != null) item { ErrorBanner(reviewError!!) }

            item {
                GlassCard(shape = RoundedCornerShape(18.dp)) {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Column(Modifier.weight(1f)) {
                            Text("自动审核", style = MaterialTheme.typography.titleMedium)
                            Spacer(Modifier.height(3.dp))
                            Text("重新审核会新增一个批次，旧批次保留", color = DatappMuted, fontSize = 11.5.sp)
                        }
                    }
                    Spacer(Modifier.height(12.dp))
                    Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                        Button(
                            onClick = {
                                action("自动审核", after = { loadReview(); onContentChanged() }) {
                                    api.reviewContent(content.contentId, force = false)
                                }
                            },
                            modifier = Modifier.weight(1f).height(42.dp),
                            enabled = busy == null,
                            shape = RoundedCornerShape(12.dp),
                            colors = ButtonDefaults.buttonColors(containerColor = accentColor),
                        ) {
                            Text("执行审核", color = Color.White, fontSize = 12.sp, fontWeight = FontWeight.Bold)
                        }
                        Button(
                            onClick = {
                                batchId = null
                                action("重新审核", after = { loadReview(); onContentChanged() }) {
                                    api.reviewContent(content.contentId, force = true)
                                }
                            },
                            modifier = Modifier.weight(1f).height(42.dp),
                            enabled = busy == null,
                            shape = RoundedCornerShape(12.dp),
                            colors = ButtonDefaults.buttonColors(containerColor = Color.White),
                            border = BorderStroke(1.dp, accentColor.copy(alpha = 0.4f)),
                        ) {
                            Text("重新审核", color = accentColor, fontSize = 12.sp, fontWeight = FontWeight.Bold)
                        }
                    }
                }
            }

            val detail = review
            if (detail != null && detail.batches.isNotEmpty()) {
                item {
                    Column {
                        Text("审核批次", style = MaterialTheme.typography.titleMedium)
                        Spacer(Modifier.height(8.dp))
                        Row(
                            Modifier.horizontalScroll(rememberScrollState()),
                            horizontalArrangement = Arrangement.spacedBy(8.dp),
                        ) {
                            detail.batches.forEach { batch ->
                                val isCurrent = if (batchId == null) batch.active else batch.batchId == batchId
                                Surface(
                                    onClick = { batchId = batch.batchId },
                                    shape = RoundedCornerShape(10.dp),
                                    color = if (isCurrent) accentColor.copy(alpha = 0.14f) else Color.White.copy(alpha = 0.75f),
                                    border = BorderStroke(1.dp, if (isCurrent) accentColor.copy(alpha = 0.4f) else Color.White),
                                ) {
                                    Text(
                                        text = "批次 ${batch.batchSeq} · ${batch.claimCount} 条" + if (batch.active) " ✓" else "",
                                        color = if (isCurrent) accentColor else DatappMuted,
                                        fontSize = 11.sp,
                                        fontWeight = FontWeight.SemiBold,
                                        modifier = Modifier.padding(horizontal = 12.dp, vertical = 6.dp),
                                    )
                                }
                            }
                        }
                        if (detail.batches.any { !it.active } && batchId != null) {
                            Spacer(Modifier.height(8.dp))
                            TextButton(
                                onClick = {
                                    action("切换生效批次", after = { loadReview(); onContentChanged() }) {
                                        api.activateBatch(content.contentId, batchId!!)
                                    }
                                },
                                contentPadding = PaddingValues(0.dp),
                            ) {
                                Text("把当前批次设为生效版本", color = accentColor, fontSize = 11.5.sp, fontWeight = FontWeight.SemiBold)
                            }
                        }
                    }
                }
            }

            if (reviewLoading && review == null) {
                item { LoadingRow("正在读取审核断言…", accentColor) }
            } else if (detail == null || detail.claims.isEmpty()) {
                item { EmptyCard("还没有审核断言", "点上面的「执行审核」抽取可验证断言") }
            } else {
                item {
                    Text("断言与证据（${detail.claims.size}）", style = MaterialTheme.typography.titleMedium)
                }
                itemsIndexed(
                    items = detail.claims,
                    key = { index, item -> "claim-${item.claim.claimId}_$index" },
                ) { _, snapshot ->
                    ClaimCard(
                        snapshot = snapshot,
                        accentColor = accentColor,
                        busy = busy != null,
                        onDecide = { status ->
                            action("人工改判", after = { loadReview(); onContentChanged() }) {
                                api.decideClaim(snapshot.claim.claimId, status)
                            }
                        },
                    )
                }
            }
        }

        // ---- 报告面板 ----
        if (panel == "报告") {
            if (reportsError != null) item { ErrorBanner(reportsError!!) }

            item {
                GlassCard(shape = RoundedCornerShape(18.dp)) {
                    Text("生成报告", style = MaterialTheme.typography.titleMedium)
                    Spacer(Modifier.height(4.dp))
                    Text(
                        "以这条内容为范围生成 JSON + Markdown 双格式报告",
                        color = DatappMuted,
                        fontSize = 11.5.sp,
                    )
                    Spacer(Modifier.height(12.dp))
                    Button(
                        onClick = {
                            action("生成报告", after = { loadReports() }) {
                                api.createReport(listOf(content.contentId), content.title.take(20))
                            }
                        },
                        modifier = Modifier.fillMaxWidth().height(44.dp),
                        enabled = busy == null,
                        shape = RoundedCornerShape(13.dp),
                        colors = ButtonDefaults.buttonColors(containerColor = accentColor),
                    ) {
                        Text("生成报告", color = Color.White, fontSize = 13.sp, fontWeight = FontWeight.Bold)
                    }
                }
            }

            if (reportsLoading && reports.isEmpty()) {
                item { LoadingRow("正在读取报告…", accentColor) }
            } else if (reports.isEmpty()) {
                item { EmptyCard("这条内容还没有报告", "点上面的「生成报告」产出一份") }
            } else {
                itemsIndexed(
                    items = reports,
                    key = { index, item -> "report-${item.reportId}_$index" },
                ) { _, item ->
                    ReportCard(
                        report = item,
                        accentColor = accentColor,
                        onClick = { onOpenReport(item.reportId) },
                    )
                }
            }
        }
    }

    // 二次确认。文案的重点不是「删不删」，而是**告诉用户这不是真删** ——
    // 服务端只打一个隐藏标记，断言/分析/报告/知识库文档全部原样保留，
    // web 看板照常可见、随时可以重新同步回 app。不说清楚，用户会以为数据没了。
    if (confirmHide) {
        AlertDialog(
            onDismissRequest = { confirmHide = false },
            title = { Text("从 app 移除这条内容？", fontSize = 16.sp, fontWeight = FontWeight.Bold) },
            text = {
                Text(
                    "它只是从 app 端隐藏 —— 断言、分析、报告、知识库文档全部保留，" +
                        "Web 看板照常可见，需要时可以由管理员重新同步回 app。",
                    fontSize = 12.5.sp,
                    lineHeight = 19.sp,
                )
            },
            confirmButton = {
                TextButton(onClick = {
                    confirmHide = false
                    // 成功后退回列表：这条内容从 app 消失了，留在详情页看一份
                    // 「即将消失」的数据没有意义
                    action("删除", after = { onContentChanged(); onBack() }) {
                        api.hideContent(content.contentId)
                    }
                }) {
                    Text("从 app 移除", color = StatusRejected, fontSize = 13.sp, fontWeight = FontWeight.Bold)
                }
            },
            dismissButton = {
                TextButton(onClick = { confirmHide = false }) {
                    Text("取消", color = DatappMuted, fontSize = 13.sp)
                }
            },
        )
    }
}

// ---------------------------------------------------------------- 面板内容

/**
 * 分析结论面板。
 *
 * **只渲染结构化结论，不渲染 markdown。** 分析 markdown 与服务端 payload 是同一份内容的
 * 两种排版，其中大半是「审计原文」JSON 块与 ID/版本/时间戳 —— 那是服务端审计用的，
 * app 是结果消费端。这块的 ID 与版本收在下方折叠的「数据存证」里。
 */
@Composable
private fun AnalysisPanel(analysis: Analysis, accentColor: Color) {
    val verdictColor = when (analysis.verdict) {
        "viral" -> StatusApproved
        "flat" -> StatusRejected
        else -> StatusUncertain
    }

    GlassCard(
        shape = RoundedCornerShape(20.dp),
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text("分析结论", style = MaterialTheme.typography.titleMedium, modifier = Modifier.weight(1f))
            StatusChip(analysis.verdictLabel, verdictColor)
        }
        Spacer(Modifier.height(10.dp))
        ConfidenceBar(analysis.confidence.toFloat(), verdictColor, label = "结论置信度")
        if (analysis.baselineCount > 0) {
            Spacer(Modifier.height(6.dp))
            Text("对比基线 ${analysis.baselineCount} 条", color = DatappMuted, fontSize = 11.sp)
        }
        if (analysis.topic.isNotBlank()) {
            Spacer(Modifier.height(10.dp))
            Text("选题方向: ${analysis.topic}", color = DatappBlueDeep, fontSize = 12.sp, fontWeight = FontWeight.SemiBold)
        }
        if (analysis.summary.isNotBlank()) {
            Spacer(Modifier.height(8.dp))
            Text(analysis.summary, color = Color(0xFF4A5A6D), fontSize = 13.sp, lineHeight = 20.sp)
        }

        // 归因：爆款读 viral_reasons、平淡读 flat_reasons —— 由模型按 verdict 二选一给出
        val reasons = analysis.attributionReasons
        if (reasons.isNotEmpty()) {
            Spacer(Modifier.height(14.dp))
            Text(analysis.attributionTitle, color = DatappInk, fontSize = 13.sp, fontWeight = FontWeight.Bold)
            reasons.forEach { reason ->
                Spacer(Modifier.height(9.dp))
                Text(reason.factor, color = Color(0xFF4A5A6D), fontSize = 12.sp, lineHeight = 18.sp, fontWeight = FontWeight.Medium)
                if (reason.evidence.isNotBlank()) {
                    Spacer(Modifier.height(2.dp))
                    Text(reason.evidence, color = DatappMuted, fontSize = 11.sp, lineHeight = 17.sp)
                }
                Spacer(Modifier.height(5.dp))
                ConfidenceBar(reason.confidence.toFloat(), accentColor)
            }
        }

        if (analysis.hooks.isNotEmpty()) {
            Spacer(Modifier.height(14.dp))
            Text("开头钩子", color = DatappInk, fontSize = 13.sp, fontWeight = FontWeight.Bold)
            Spacer(Modifier.height(6.dp))
            ChipRow(analysis.hooks, accentColor)
        }
        if (analysis.audience.isNotEmpty()) {
            Spacer(Modifier.height(12.dp))
            Text("目标人群", color = DatappInk, fontSize = 13.sp, fontWeight = FontWeight.Bold)
            Spacer(Modifier.height(6.dp))
            ChipRow(analysis.audience, DatappMuted)
        }

        if (analysis.suggestions.isNotEmpty()) {
            Spacer(Modifier.height(14.dp))
            Text("改进建议", color = DatappInk, fontSize = 13.sp, fontWeight = FontWeight.Bold)
            Spacer(Modifier.height(6.dp))
            BulletList(analysis.suggestions, accentColor)
        }

        // 局限留着：它讲的是这个结论在什么条件下不成立，是判断可信度的必要信息，
        // 不是无关文字 —— 与「模型输出 ≠ 事实」这条红线同源。
        if (analysis.limitations.isNotEmpty()) {
            Spacer(Modifier.height(14.dp))
            Text("局限", color = DatappInk, fontSize = 13.sp, fontWeight = FontWeight.Bold)
            Spacer(Modifier.height(6.dp))
            BulletList(analysis.limitations, DatappMuted, textColor = DatappMuted)
        }
    }
}

@Composable
private fun ClaimCard(
    snapshot: ClaimSnapshot,
    accentColor: Color,
    busy: Boolean,
    onDecide: (String) -> Unit,
) {
    val claim = snapshot.claim
    val color = claimStatusColor(claim.status)

    GlassCard(shape = RoundedCornerShape(18.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            StatusChip(claim.statusLabel, color)
            Spacer(Modifier.weight(1f))
            if (claim.claimType.isNotBlank()) StatusChip(claim.claimType, DatappMuted)
        }

        Spacer(Modifier.height(9.dp))
        Text(claim.text, color = DatappInk, fontSize = 13.sp, lineHeight = 20.sp, fontWeight = FontWeight.Medium)

        Spacer(Modifier.height(10.dp))
        // 确认度低到推不动结论时，颜色跟着变 —— 光看百分比容易被忽略
        ConfidenceBar(
            (claim.confidence ?: 0.0).toFloat(),
            if (claim.isLowConfidence) StatusUncertain else color,
            label = if (claim.isLowConfidence) "断言置信度 · 低于阈值" else "断言置信度",
        )

        if (snapshot.evidence.isNotEmpty()) {
            Spacer(Modifier.height(12.dp))
            Text("证据（${snapshot.evidence.size}）", color = DatappMuted, fontSize = 11.sp, fontWeight = FontWeight.SemiBold)
            snapshot.evidence.forEach { ev ->
                Spacer(Modifier.height(5.dp))
                Surface(
                    shape = RoundedCornerShape(10.dp),
                    color = DatappSurfaceSoft,
                ) {
                    Column(Modifier.padding(horizontal = 10.dp, vertical = 8.dp)) {
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            // 来源是 content/platform/external/human 机码，转中文再显示
                            Text(
                                sourceKindLabel(ev.sourceKind),
                                color = accentColor,
                                fontSize = 10.sp,
                                fontWeight = FontWeight.SemiBold,
                            )
                            // 这条证据是**支持**还是**反驳**该断言 —— 审核面板最该看见的一栏，
                            // 此前解析了却从没展示过。supports 为 null 表示模型没给方向，不硬凑。
                            if (ev.supports != null) {
                                Spacer(Modifier.width(6.dp))
                                StatusChip(
                                    if (ev.supports) "支持" else "反驳",
                                    if (ev.supports) StatusApproved else StatusRejected,
                                )
                            }
                        }
                        Spacer(Modifier.height(4.dp))
                        Text(ev.excerpt, color = Color(0xFF55667A), fontSize = 11.5.sp, lineHeight = 17.sp)
                    }
                }
            }
        } else {
            Spacer(Modifier.height(8.dp))
            Text("暂无外部证据", color = DatappMuted, fontSize = 11.sp)
        }

        Spacer(Modifier.height(12.dp))
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            ClaimDecisionButton("支持", StatusApproved, busy) { onDecide("supported") }
            ClaimDecisionButton("反驳", StatusRejected, busy) { onDecide("contradicted") }
            ClaimDecisionButton("存疑", StatusUncertain, busy) { onDecide("unclear") }
        }
    }
}

@Composable
private fun ClaimDecisionButton(
    label: String,
    color: Color,
    busy: Boolean,
    onClick: () -> Unit,
) {
    Surface(
        onClick = onClick,
        enabled = !busy,
        shape = RoundedCornerShape(10.dp),
        color = color.copy(alpha = 0.12f),
        border = BorderStroke(1.dp, color.copy(alpha = 0.35f)),
    ) {
        Text(
            text = label,
            color = color,
            fontSize = 11.5.sp,
            fontWeight = FontWeight.SemiBold,
            modifier = Modifier.padding(horizontal = 16.dp, vertical = 7.dp),
        )
    }
}

@Composable
private fun NoticeBanner(text: String, ok: Boolean, accentColor: Color) {
    val color = if (ok) accentColor else StatusRejected
    Surface(
        modifier = Modifier.fillMaxWidth(),
        shape = RoundedCornerShape(14.dp),
        color = color.copy(alpha = 0.09f),
        border = BorderStroke(1.dp, color.copy(alpha = 0.3f)),
    ) {
        Row(Modifier.padding(horizontal = 14.dp, vertical = 11.dp), verticalAlignment = Alignment.Top) {
            Text(if (ok) "✓" else "!", color = color, fontSize = 13.sp, fontWeight = FontWeight.Bold)
            Spacer(Modifier.width(9.dp))
            Text(text, color = Color(0xFF4A5A6D), fontSize = 12.sp, lineHeight = 18.sp)
        }
    }
}
