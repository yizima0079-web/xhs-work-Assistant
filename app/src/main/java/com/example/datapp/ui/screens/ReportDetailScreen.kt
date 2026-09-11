package com.example.datapp.ui.screens

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.example.datapp.data.DatappRepository
import com.example.datapp.data.ReportDetail
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
import com.example.datapp.ui.theme.DatappInk
import com.example.datapp.ui.theme.DatappMuted
import com.example.datapp.ui.theme.StatusApproved
import com.example.datapp.ui.theme.StatusRejected
import com.example.datapp.ui.theme.StatusUncertain
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

/**
 * 报告详情。
 *
 * **结论全部来自 payload，不渲染 markdown。** 服务端产出 JSON + Markdown 双格式（可审计），
 * 那两份是同一份结论的两种排版；markdown 那份还带着「审计原文」JSON 块、报告 ID、分析版本
 * 与时间戳 —— 服务端与 web 看板要那份做审计，app 是结果消费端，只要结构化结论。
 *
 * 元信息（报告 ID / 覆盖内容 ID / 分析版本）没有删，收在末尾**默认收起**的「数据存证」卡里：
 * 结论看着不对劲时，得能拿 ID 回服务端查证。
 *
 * 顺序即阅读动线：摘要 → 爆点模式 → 表现风格 → 账号人设 → 趋势 → 局限 → 存证。
 */
@Composable
fun ReportDetailScreen(
    reportId: String,
    repository: DatappRepository,
    accentColor: Color,
    onBack: () -> Unit,
) {
    var report by remember { mutableStateOf<ReportDetail?>(null) }
    var error by remember { mutableStateOf<String?>(null) }
    var loading by remember { mutableStateOf(true) }

    LaunchedEffect(reportId) {
        loading = true
        val (detail, err) = withContext(Dispatchers.IO) { repository.fetchReportDetail(reportId) }
        report = detail
        error = err
        loading = false
    }

    LazyColumn(
        modifier = Modifier.fillMaxSize(),
        contentPadding = PaddingValues(start = 20.dp, end = 20.dp, top = 18.dp, bottom = 96.dp),
        verticalArrangement = Arrangement.spacedBy(16.dp),
    ) {
        item { BackHeader("报告详情", accentColor, onBack) }

        when {
            loading -> item { LoadingRow("正在读取报告…", accentColor) }
            error != null -> item { ErrorBanner(error!!) }
            report == null -> item { EmptyCard("报告不存在", "该报告可能已被删除") }
            else -> {
                val r = report!!
                item {
                    Column {
                        Text(r.title, style = MaterialTheme.typography.titleLarge)
                        Spacer(Modifier.height(5.dp))
                        Text("${r.createdAt} · 覆盖 ${r.contentIds.size} 条内容", color = DatappMuted, fontSize = 12.sp)
                    }
                }

                if (!r.hasStructuredContent) {
                    // 老报告可能没有 payload。这里**不回落去铺 markdown** ——
                    // 那正是这轮要拆掉的东西；如实说没有结构化结论，比糊一篇文档强。
                    item { EmptyCard("这份报告没有结构化结论", "它由更早的版本生成，原始 markdown 可在 web 看板查看") }
                } else {
                    if (r.summary.isNotBlank()) {
                        item {
                            GlassCard(
                                shape = RoundedCornerShape(22.dp),
                            ) {
                                Text("摘要", style = MaterialTheme.typography.titleMedium)
                                Spacer(Modifier.height(8.dp))
                                Text(r.summary, color = Color(0xFF4A5A6D), fontSize = 13.5.sp, lineHeight = 21.sp)
                            }
                        }
                    }

                    if (r.viralPatterns.isNotEmpty()) {
                        item {
                            Text("爆点模式", style = MaterialTheme.typography.titleMedium)
                        }
                        r.viralPatterns.forEach { pattern ->
                            item {
                                GlassCard(shape = RoundedCornerShape(18.dp)) {
                                    Text(
                                        pattern.pattern,
                                        color = DatappInk,
                                        fontSize = 13.sp,
                                        lineHeight = 20.sp,
                                        fontWeight = FontWeight.Medium,
                                    )
                                    Spacer(Modifier.height(9.dp))
                                    ConfidenceBar(
                                        pattern.confidence.toFloat(),
                                        accentColor,
                                        label = "该模式成立度",
                                    )
                                    // 反例条数照实说：模式不是普适规律，有反例就该让人看见有几条
                                    if (pattern.counterexampleCount > 0) {
                                        Spacer(Modifier.height(6.dp))
                                        Text(
                                            "存在 ${pattern.counterexampleCount} 条反例",
                                            color = StatusUncertain,
                                            fontSize = 10.5.sp,
                                        )
                                    }
                                }
                            }
                        }
                    }

                    // 表现风格：四组里空的组在模型层就被丢了，这里只会拿到非空组
                    if (r.presentationStyle.isNotEmpty()) {
                        item {
                            GlassCard(shape = RoundedCornerShape(18.dp)) {
                                Text("表现风格", style = MaterialTheme.typography.titleMedium)
                                r.presentationStyle.forEach { (label, items) ->
                                    Spacer(Modifier.height(11.dp))
                                    Text(label, color = DatappInk, fontSize = 12.5.sp, fontWeight = FontWeight.Bold)
                                    Spacer(Modifier.height(6.dp))
                                    ChipRow(items, accentColor)
                                }
                            }
                        }
                    }

                    if (r.personaHypotheses.isNotEmpty()) {
                        item {
                            GlassCard(shape = RoundedCornerShape(18.dp)) {
                                Text("账号人设", style = MaterialTheme.typography.titleMedium)
                                Spacer(Modifier.height(8.dp))
                                BulletList(r.personaHypotheses, accentColor)
                                Spacer(Modifier.height(10.dp))
                                ConfidenceBar(r.personaConfidence.toFloat(), accentColor, label = "人设判断置信度")
                            }
                        }
                    }

                    if (r.trendTopics.isNotEmpty() || r.trendDirection.isNotBlank()) {
                        item {
                            GlassCard(shape = RoundedCornerShape(18.dp)) {
                                Row(verticalAlignment = Alignment.CenterVertically) {
                                    Text("趋势", style = MaterialTheme.typography.titleMedium, modifier = Modifier.weight(1f))
                                    if (r.trendDirection.isNotBlank()) {
                                        // rising/stable/falling/uncertain 是机码，转中文加符号再上屏
                                        StatusChip(
                                            r.trendDirectionLabel,
                                            when (r.trendDirection) {
                                                "rising" -> StatusApproved
                                                "falling" -> StatusRejected
                                                else -> StatusUncertain
                                            },
                                        )
                                    }
                                }
                                if (r.trendTopics.isNotEmpty()) {
                                    Spacer(Modifier.height(10.dp))
                                    ChipRow(r.trendTopics, accentColor)
                                }
                                if (r.trendWindow.isNotBlank()) {
                                    Spacer(Modifier.height(8.dp))
                                    Text("观察窗口: ${r.trendWindow}", color = DatappMuted, fontSize = 11.sp)
                                }
                            }
                        }
                    }

                    // 局限讲的是这份结论在什么条件下不成立 —— 判断可信度的必要信息，留着
                    if (r.limitations.isNotEmpty()) {
                        item {
                            GlassCard(shape = RoundedCornerShape(18.dp)) {
                                Text("局限", style = MaterialTheme.typography.titleMedium)
                                Spacer(Modifier.height(8.dp))
                                BulletList(r.limitations, DatappMuted, textColor = DatappMuted)
                            }
                        }
                    }
                }

                item {
                    CollapsibleCard("数据存证") {
                        AttestRow("报告 ID", r.reportId)
                        AttestRow("分析版本", r.analysisVersion.ifBlank { "—" })
                        AttestRow("生成时间", r.createdAt)
                        Spacer(Modifier.height(4.dp))
                        r.contentIds.forEachIndexed { index, id ->
                            AttestRow(if (index == 0) "覆盖内容" else "", id)
                        }
                    }
                }
            }
        }
    }
}
