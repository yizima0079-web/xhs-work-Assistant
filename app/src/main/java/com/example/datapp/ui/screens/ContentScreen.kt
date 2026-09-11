package com.example.datapp.ui.screens

import androidx.compose.foundation.background
import androidx.compose.foundation.horizontalScroll
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
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.FilterChip
import androidx.compose.material3.FilterChipDefaults
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
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
import com.example.datapp.data.Content
import com.example.datapp.ui.components.EdgeBlurBox
import com.example.datapp.ui.components.EmptyCard
import com.example.datapp.ui.components.ErrorBanner
import com.example.datapp.ui.components.GlassCard
import com.example.datapp.ui.components.LoadingRow
import com.example.datapp.ui.components.ScreenHeader
import com.example.datapp.ui.components.StatusChip
import com.example.datapp.ui.components.reviewStatusColor
import com.example.datapp.ui.theme.DatappInk
import com.example.datapp.ui.theme.DatappMuted
import com.example.datapp.ui.theme.DatappSurfaceSoft

/**
 * 内容 tab：内容池列表 + 检索过滤。
 *
 * 这里是分析 / 审核 / 报告的统一入口 —— 点进 [ContentDetailScreen] 后
 * 三个面板都在同一条内容上操作，避免"审核在 A 页、分析在 B 页"的割裂。
 */
@Composable
fun ContentScreen(
    contents: List<Content>,
    coverOf: (Content) -> String?,
    accentColor: Color,
    bgColor: Color,
    loading: Boolean,
    error: String?,
    onContent: (String) -> Unit,
    onCollect: () -> Unit,
    onRefresh: () -> Unit,
) {
    var query by rememberSaveable { mutableStateOf("") }
    var activeFilter by rememberSaveable { mutableStateOf("全部") }
    val filters = listOf("全部", "待处理", "可信", "不采信")

    val trimmed = query.trim()
    val filtered = contents.filter { c ->
        val matchFilter = when (activeFilter) {
            "待处理" -> c.reviewStatus == "pending" || c.reviewStatus == "extracted"
            "可信" -> c.reviewStatus == "approved"
            "不采信" -> c.reviewStatus == "rejected"
            else -> true
        }
        val matchQuery = trimmed.isBlank() ||
            c.title.contains(trimmed, true) ||
            c.text.contains(trimmed, true) ||
            c.authorName.contains(trimmed, true) ||
            c.tags.any { it.contains(trimmed, true) }
        matchFilter && matchQuery
    }

    EdgeBlurBox(backgroundColor = bgColor) {
        LazyColumn(
            modifier = Modifier.fillMaxSize(),
            contentPadding = PaddingValues(start = 20.dp, end = 20.dp, top = 20.dp, bottom = 96.dp),
            verticalArrangement = Arrangement.spacedBy(14.dp),
        ) {
            item {
                Column {
                    ScreenHeader(
                        title = "内容池",
                        subtitle = "共 ${contents.size} 条 · 点进去做分析、审核、出报告",
                        accentColor = accentColor,
                    )
                    Spacer(Modifier.height(14.dp))
                    // 采集检索入口：用关键词去平台搜一批新内容进来。
                    // 语义上和「筛选已采到的内容」是两件事，所以用一张独立卡片隔开，
                    // 而不是塞进下面的搜索框。
                    GlassCard(
                        modifier = Modifier.fillMaxWidth(),
                        onClick = onCollect,
                        shape = RoundedCornerShape(18.dp),
                    ) {
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            Box(
                                Modifier
                                    .size(38.dp)
                                    .clip(RoundedCornerShape(12.dp))
                                    .background(accentColor.copy(alpha = 0.12f)),
                                contentAlignment = Alignment.Center,
                            ) {
                                Text("⌕", color = accentColor, fontSize = 19.sp, fontWeight = FontWeight.Bold)
                            }
                            Spacer(Modifier.width(11.dp))
                            Column(Modifier.weight(1f)) {
                                Text(
                                    "采集检索",
                                    color = DatappInk,
                                    fontSize = 14.sp,
                                    fontWeight = FontWeight.Bold,
                                )
                                Spacer(Modifier.height(2.dp))
                                Text(
                                    "输入关键词，提交后由管理员放行采集",
                                    color = DatappMuted,
                                    fontSize = 11.5.sp,
                                )
                            }
                            Text("›", color = DatappMuted, fontSize = 22.sp, fontWeight = FontWeight.Bold)
                        }
                    }
                    Spacer(Modifier.height(14.dp))
                    OutlinedTextField(
                        value = query,
                        onValueChange = { query = it },
                        modifier = Modifier.fillMaxWidth(),
                        singleLine = true,
                        shape = RoundedCornerShape(18.dp),
                        placeholder = { Text("搜索标题、正文、作者或标签", color = DatappMuted, fontSize = 13.sp) },
                        leadingIcon = { Text("⌕", color = accentColor, fontSize = 22.sp) },
                        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Text),
                        colors = OutlinedTextFieldDefaults.colors(
                            focusedContainerColor = Color.White.copy(alpha = 0.9f),
                            unfocusedContainerColor = Color.White.copy(alpha = 0.85f),
                            focusedBorderColor = accentColor.copy(alpha = 0.6f),
                            unfocusedBorderColor = Color.White,
                        ),
                    )
                }
            }

            item {
                Row(
                    Modifier.horizontalScroll(rememberScrollState()),
                    horizontalArrangement = Arrangement.spacedBy(8.dp),
                ) {
                    filters.forEach { tag ->
                        val selected = activeFilter == tag
                        FilterChip(
                            selected = selected,
                            onClick = { activeFilter = tag },
                            label = {
                                Text(
                                    tag,
                                    fontSize = 12.sp,
                                    color = if (selected) accentColor else DatappMuted,
                                    fontWeight = if (selected) FontWeight.SemiBold else FontWeight.Normal,
                                )
                            },
                            colors = FilterChipDefaults.filterChipColors(
                                selectedContainerColor = accentColor.copy(alpha = 0.12f),
                                containerColor = Color.White.copy(alpha = 0.7f),
                            ),
                            border = FilterChipDefaults.filterChipBorder(
                                enabled = true,
                                selected = selected,
                                selectedBorderColor = accentColor.copy(alpha = 0.3f),
                                borderColor = Color.White,
                            ),
                        )
                    }
                }
            }

            if (error != null) {
                item { ErrorBanner(error) }
            }

            when {
                loading && contents.isEmpty() -> item { LoadingRow("正在同步内容池…", accentColor) }
                contents.isEmpty() -> item {
                    EmptyCard("内容池为空", "后端还没有已采集的内容，或请求失败")
                }
                filtered.isEmpty() -> item {
                    EmptyCard("没有匹配的内容", "换个关键词或清掉筛选条件")
                }
                else -> itemsIndexed(
                    items = filtered,
                    key = { index, item -> "content-${item.contentId}_$index" },
                ) { _, item ->
                    ContentCard(
                        content = item,
                        coverUrl = coverOf(item),
                        accentColor = accentColor,
                        onClick = { onContent(item.contentId) },
                    )
                }
            }

            if (contents.isNotEmpty()) {
                item {
                    Row(
                        Modifier.fillMaxWidth().padding(top = 8.dp),
                        horizontalArrangement = Arrangement.Center,
                    ) {
                        androidx.compose.material3.TextButton(onClick = onRefresh) {
                            Text("重新同步", color = accentColor, fontSize = 12.sp, fontWeight = FontWeight.SemiBold)
                        }
                    }
                }
            }
        }
    }
}

/** 内容卡片：封面 + 标题 + 作者 + 审核状态 + 互动数据 */
@OptIn(ExperimentalLayoutApi::class)
@Composable
internal fun ContentCard(
    content: Content,
    coverUrl: String?,
    accentColor: Color,
    onClick: () -> Unit,
) {
    val statusColor = reviewStatusColor(content.reviewStatus)

    GlassCard(
        modifier = Modifier.fillMaxWidth(),
        onClick = onClick,
        shape = RoundedCornerShape(20.dp),
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            if (!coverUrl.isNullOrBlank()) {
                AsyncImage(
                    model = coverUrl,
                    contentDescription = content.title,
                    modifier = Modifier
                        .size(58.dp)
                        .clip(RoundedCornerShape(14.dp))
                        .background(DatappSurfaceSoft),
                    contentScale = ContentScale.Crop,
                )
            } else {
                Box(
                    Modifier
                        .size(58.dp)
                        .clip(RoundedCornerShape(14.dp))
                        .background(accentColor.copy(alpha = 0.12f)),
                    contentAlignment = Alignment.Center,
                ) {
                    Text("📄", fontSize = 22.sp)
                }
            }

            Spacer(Modifier.width(12.dp))

            Column(Modifier.weight(1f)) {
                Text(
                    text = content.title,
                    color = DatappInk,
                    fontSize = 14.sp,
                    fontWeight = FontWeight.Bold,
                    maxLines = 2,
                    overflow = TextOverflow.Ellipsis,
                )
                Spacer(Modifier.height(4.dp))
                Text(
                    text = "${content.authorName} · ${content.publishedAt}",
                    color = DatappMuted,
                    fontSize = 11.sp,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                )
                Spacer(Modifier.height(6.dp))
                FlowRow(
                    horizontalArrangement = Arrangement.spacedBy(6.dp),
                    verticalArrangement = Arrangement.spacedBy(6.dp),
                ) {
                    StatusChip(content.reviewStatusLabel, statusColor)
                    if (content.likes.isNotBlank() && content.likes != "0") {
                        StatusChip("♥ ${content.likes}", accentColor)
                    }
                    content.tags.take(2).forEach { tag ->
                        StatusChip(tag, DatappMuted)
                    }
                }
            }

            Spacer(Modifier.width(6.dp))
            Text("›", color = DatappMuted, fontSize = 22.sp, fontWeight = FontWeight.Bold)
        }
    }
}
