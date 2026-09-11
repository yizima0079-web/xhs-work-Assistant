package com.example.datapp.ui.screens

import androidx.compose.animation.core.Animatable
import androidx.compose.animation.core.LinearEasing
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.rotate
import androidx.compose.ui.draw.scale
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.input.nestedscroll.NestedScrollConnection
import androidx.compose.ui.input.nestedscroll.NestedScrollSource
import androidx.compose.ui.input.nestedscroll.nestedScroll
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.IntOffset
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.zIndex
import com.example.datapp.data.Content
import com.example.datapp.data.ReportDetail
import com.example.datapp.data.SummaryStats
import com.example.datapp.ui.components.EdgeBlurBox
import com.example.datapp.ui.components.EmptyCard
import com.example.datapp.ui.components.ErrorBanner
import com.example.datapp.ui.components.GlassCard
import com.example.datapp.ui.components.LoadingRow
import com.example.datapp.ui.components.MetricCard
import com.example.datapp.ui.components.SectionHeader
import com.example.datapp.ui.theme.DatappBlueDeep
import com.example.datapp.ui.theme.DatappCyan
import com.example.datapp.ui.theme.DatappInk
import com.example.datapp.ui.theme.DatappLine
import com.example.datapp.ui.theme.DatappMuted
import com.example.datapp.ui.theme.ThemeContentAccent
import com.example.datapp.ui.theme.ThemeInsightsAccent
import com.example.datapp.ui.theme.ThemeReportsAccent
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.text.SimpleDateFormat
import java.util.Calendar
import java.util.Date
import java.util.Locale
import kotlin.math.roundToInt

/**
 * 首页：粗预览 + 跳转入口。
 *
 * 这一页只放「一眼能看懂的汇总」，具体操作都在内容 / 知识库两个 tab 里。
 * 所有数字都来自真实数据，不做任何凑数 —— 空库就显示 0。
 */
@Composable
fun HomeScreen(
    username: String,
    contents: List<Content>,
    reports: List<ReportDetail>,
    kbDocumentCount: Int,
    stats: SummaryStats,
    coverOf: (Content) -> String?,
    accentColor: Color,
    bgColor: Color,
    loading: Boolean,
    error: String?,
    onContent: (String) -> Unit,
    onReport: (String) -> Unit,
    onGoContent: () -> Unit,
    onGoKnowledge: () -> Unit,
    onRefresh: suspend () -> Unit,
) {
    HomePullToRefreshContainer(accentColor = accentColor, onRefreshData = onRefresh) {
        EdgeBlurBox(backgroundColor = bgColor) {
            LazyColumn(
                modifier = Modifier.fillMaxSize(),
                contentPadding = PaddingValues(start = 20.dp, end = 20.dp, top = 20.dp, bottom = 96.dp),
                verticalArrangement = Arrangement.spacedBy(16.dp),
            ) {
                item {
                    Column {
                        Text(todayLabel(), color = DatappMuted, fontSize = 12.sp)
                        Spacer(Modifier.height(6.dp))
                        Text("${greeting()}，$username", style = MaterialTheme.typography.titleLarge)
                        Spacer(Modifier.height(4.dp))
                        Text("今天也来看看你的内容风向", color = DatappMuted, fontSize = 13.sp)
                    }
                }

                if (error != null) {
                    item { ErrorBanner(error) }
                }

                item { WeeklyInsightCard(stats, accentColor) }

                item {
                    Row(horizontalArrangement = Arrangement.spacedBy(12.dp), modifier = Modifier.fillMaxWidth()) {
                        MetricCard(
                            "待复核内容",
                            "${stats.pendingReviewCount}",
                            "需要你的判断",
                            ThemeReportsAccent,
                            Modifier.weight(1f),
                        )
                        MetricCard(
                            "知识库文档",
                            "$kbDocumentCount",
                            "可检索的知识原子",
                            ThemeInsightsAccent,
                            Modifier.weight(1f),
                        )
                    }
                }

                item { SectionHeader("最近内容", "全部内容", accentColor, onClick = onGoContent) }
                when {
                    loading && contents.isEmpty() -> item { LoadingRow("正在同步内容池…", accentColor) }
                    contents.isEmpty() -> item {
                        EmptyCard("内容池为空", "后端还没有已采集的内容，或本次请求失败")
                    }
                    else -> itemsIndexed(
                        items = contents.take(3),
                        key = { index, item -> "home-content-${item.contentId}_$index" },
                    ) { _, item ->
                        ContentCard(
                            content = item,
                            coverUrl = coverOf(item),
                            accentColor = accentColor,
                            onClick = { onContent(item.contentId) },
                        )
                    }
                }

                item { SectionHeader("最近报告", "全部报告", accentColor, onClick = onGoContent) }
                when {
                    loading && reports.isEmpty() -> item { LoadingRow("正在同步报告…", accentColor) }
                    reports.isEmpty() -> item {
                        EmptyCard("还没有报告", "到内容详情里选中内容即可生成审核报告")
                    }
                    else -> itemsIndexed(
                        items = reports.take(3),
                        key = { index, item -> "home-report-${item.reportId}_$index" },
                    ) { _, item ->
                        ReportCard(report = item, accentColor = accentColor, onClick = { onReport(item.reportId) })
                    }
                }

                item {
                    GlassCard(
                        modifier = Modifier.fillMaxWidth(),
                        shape = RoundedCornerShape(20.dp),
                        onClick = onGoKnowledge,
                    ) {
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            Column(Modifier.weight(1f)) {
                                Text("知识库检索与问答", color = DatappInk, fontSize = 14.sp, fontWeight = FontWeight.Bold)
                                Spacer(Modifier.height(3.dp))
                                Text("语义检索命中已审核内容，带引用作答", color = DatappMuted, fontSize = 11.5.sp)
                            }
                            Text("→", color = ThemeInsightsAccent, fontSize = 22.sp, fontWeight = FontWeight.Bold)
                        }
                    }
                }

                item {
                    Text(
                        "下拉可重新同步后端最新数据",
                        modifier = Modifier.fillMaxWidth().padding(vertical = 12.dp),
                        color = DatappMuted,
                        fontSize = 12.sp,
                    )
                }
            }
        }
    }
}

// ---------------------------------------------------------------- 卡片

@Composable
private fun WeeklyInsightCard(stats: SummaryStats, accentColor: Color) {
    GlassCard(
        shape = RoundedCornerShape(22.dp),
        modifier = Modifier.fillMaxWidth(),
    ) {
        Column {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Column(Modifier.weight(1f)) {
                    Text("内容池概览", color = DatappMuted, fontSize = 12.sp)
                    Spacer(Modifier.height(6.dp))
                    Row(verticalAlignment = Alignment.Bottom) {
                        Text(
                            "${stats.totalContentsCount}",
                            color = DatappBlueDeep,
                            fontSize = 32.sp,
                            fontWeight = FontWeight.Bold,
                        )
                        Spacer(Modifier.width(6.dp))
                        Text(
                            "条内容",
                            color = DatappBlueDeep,
                            fontSize = 13.sp,
                            modifier = Modifier.padding(bottom = 5.dp),
                        )
                    }
                }
                Text("✓", color = ThemeInsightsAccent, fontSize = 24.sp, fontWeight = FontWeight.Bold)
            }
            Spacer(Modifier.height(12.dp))
            Row(verticalAlignment = Alignment.CenterVertically) {
                Box(
                    Modifier
                        .weight(1f)
                        .height(6.dp)
                        .clip(RoundedCornerShape(6.dp))
                        .background(DatappLine)
                ) {
                    Box(
                        Modifier
                            .fillMaxWidth(stats.reviewedRatio.coerceIn(0f, 1f))
                            .height(6.dp)
                            .background(Brush.horizontalGradient(listOf(DatappCyan, accentColor)))
                    )
                }
                Spacer(Modifier.width(10.dp))
                Text("已审核 ${stats.reviewedRatioLabel}", color = DatappMuted, fontSize = 11.sp)
            }
            Spacer(Modifier.height(6.dp))
            Text(
                "可信 ${stats.approvedCount} · 不采信 ${stats.rejectedCount} · 待处理 ${stats.pendingReviewCount}",
                color = DatappMuted,
                fontSize = 11.sp,
            )
        }
    }
}

/** 报告卡片：只展示标题 + 时间 + markdown 首段，正文进详情看 */
@Composable
internal fun ReportCard(report: ReportDetail, accentColor: Color, onClick: () -> Unit) {
    GlassCard(
        modifier = Modifier.fillMaxWidth(),
        onClick = onClick,
        shape = RoundedCornerShape(20.dp),
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(
                report.title,
                color = DatappInk,
                fontSize = 14.sp,
                fontWeight = FontWeight.SemiBold,
                modifier = Modifier.weight(1f),
                maxLines = 1,
                overflow = androidx.compose.ui.text.style.TextOverflow.Ellipsis,
            )
            Text("▤", color = accentColor, fontSize = 16.sp)
        }
        Spacer(Modifier.height(4.dp))
        Text(
            "${report.createdAt} · ${report.contentIds.size} 条内容",
            color = DatappMuted,
            fontSize = 11.sp,
            maxLines = 1,
        )
        // 摘要直接读 payload 的 executive_summary —— 比从 markdown 里挖首行准，也不用解析排版
        if (report.summary.isNotBlank()) {
            Spacer(Modifier.height(10.dp))
            Text(
                report.summary,
                color = Color(0xFF55667A),
                fontSize = 12.sp,
                lineHeight = 18.sp,
                maxLines = 2,
                overflow = androidx.compose.ui.text.style.TextOverflow.Ellipsis,
            )
        }
    }
}

// ---------------------------------------------------------------- 下拉刷新

/**
 * 首页下拉刷新框架：指示器绝对置顶 (zIndex = 100f)
 */
@Composable
private fun HomePullToRefreshContainer(
    accentColor: Color,
    onRefreshData: suspend () -> Unit,
    content: @Composable () -> Unit,
) {
    val coroutineScope = rememberCoroutineScope()
    var isRefreshing by remember { mutableStateOf(false) }
    var pullOffsetY by remember { mutableFloatStateOf(0f) }

    val pullAnim = remember { Animatable(0f) }
    val maxPullPx = 220f
    val thresholdPx = 130f

    LaunchedEffect(pullOffsetY) {
        pullAnim.snapTo(pullOffsetY)
    }

    fun triggerRefresh() {
        if (isRefreshing) return
        isRefreshing = true
        coroutineScope.launch {
            withContext(Dispatchers.IO) { onRefreshData() }
            delay(500)
            isRefreshing = false
            val anim = Animatable(pullOffsetY)
            anim.animateTo(0f, tween(300)) { pullOffsetY = value }
        }
    }

    val nestedScrollConnection = remember {
        object : NestedScrollConnection {
            override fun onPreScroll(available: Offset, source: NestedScrollSource): Offset {
                if (pullOffsetY > 0f && available.y < 0f) {
                    val consume = available.y
                    pullOffsetY = (pullOffsetY + consume).coerceAtLeast(0f)
                    return Offset(0f, consume)
                }
                return Offset.Zero
            }

            override fun onPostScroll(consumed: Offset, available: Offset, source: NestedScrollSource): Offset {
                if (available.y > 0f && !isRefreshing) {
                    pullOffsetY = (pullOffsetY + available.y * 0.45f).coerceAtMost(maxPullPx)
                    return Offset(0f, available.y)
                }
                return Offset.Zero
            }

            override suspend fun onPreFling(available: androidx.compose.ui.unit.Velocity): androidx.compose.ui.unit.Velocity {
                if (pullOffsetY >= thresholdPx && !isRefreshing) {
                    triggerRefresh()
                } else if (!isRefreshing) {
                    val anim = Animatable(pullOffsetY)
                    anim.animateTo(0f, tween(250)) { pullOffsetY = value }
                }
                return androidx.compose.ui.unit.Velocity.Zero
            }
        }
    }

    val currentPullProgress = (pullOffsetY / thresholdPx).coerceIn(0f, 1f)

    Box(
        modifier = Modifier
            .fillMaxSize()
            .nestedScroll(nestedScrollConnection)
    ) {
        Box(
            modifier = Modifier
                .fillMaxSize()
                .offset { IntOffset(0, pullOffsetY.roundToInt()) }
        ) {
            content()
        }

        if (pullOffsetY > 0f || isRefreshing) {
            val infiniteTransition = rememberInfiniteTransition(label = "refresh-spin")
            val spinRotation by infiniteTransition.animateFloat(
                initialValue = 0f,
                targetValue = 360f,
                animationSpec = infiniteRepeatable(
                    animation = tween(800, easing = LinearEasing),
                    repeatMode = RepeatMode.Restart
                ),
                label = "spin",
            )

            val rotationAngle = if (isRefreshing) spinRotation else currentPullProgress * 360f
            val iconScale = if (isRefreshing) 1f else (0.5f + currentPullProgress * 0.5f)

            Surface(
                modifier = Modifier
                    .align(Alignment.TopCenter)
                    .zIndex(100f)
                    .offset { IntOffset(0, (pullOffsetY.roundToInt() - 44).coerceAtLeast(12)) }
                    .scale(iconScale),
                shape = CircleShape,
                color = Color.White,
                border = BorderStroke(1.5.dp, accentColor.copy(alpha = 0.35f)),
                shadowElevation = 8.dp,
            ) {
                Row(
                    modifier = Modifier.padding(horizontal = 16.dp, vertical = 9.dp),
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(8.dp),
                ) {
                    Text(
                        text = "↻",
                        fontSize = 18.sp,
                        color = accentColor,
                        fontWeight = FontWeight.Bold,
                        modifier = Modifier.rotate(rotationAngle),
                    )
                    Text(
                        text = when {
                            isRefreshing -> "正在同步…"
                            pullOffsetY >= thresholdPx -> "松开立即更新"
                            else -> "下拉刷新"
                        },
                        fontSize = 12.sp,
                        color = DatappInk,
                        fontWeight = FontWeight.SemiBold,
                    )
                }
            }
        }
    }
}

// ---------------------------------------------------------------- 小工具

private fun todayLabel(): String =
    SimpleDateFormat("EEEE · M月d日", Locale.CHINA).format(Date())

private fun greeting(): String = when (Calendar.getInstance().get(Calendar.HOUR_OF_DAY)) {
    in 5..11 -> "早上好"
    in 12..13 -> "中午好"
    in 14..18 -> "下午好"
    else -> "晚上好"
}
