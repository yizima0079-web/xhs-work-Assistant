package com.example.datapp.ui.components

import androidx.compose.animation.animateContentSize
import androidx.compose.animation.core.Spring
import androidx.compose.animation.core.animateDpAsState
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.spring
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.interaction.collectIsPressedAsState
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.scale
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.text.withStyle
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.example.datapp.ui.theme.DatappInk
import com.example.datapp.ui.theme.DatappLine
import com.example.datapp.ui.theme.DatappMuted
import com.example.datapp.ui.theme.StatusApproved
import com.example.datapp.ui.theme.StatusPending
import com.example.datapp.ui.theme.StatusRejected
import com.example.datapp.ui.theme.StatusUncertain

/**
 * 跨页面复用的视觉组件。
 *
 * 这些原先是 ui/DatappApp.kt 里的 private 组件；拆屏后被多个 screen 共用，
 * 因此提到独立文件并放开为 internal。视觉规则一字未改，仅换位置。
 */

/** 边缘淡化蒙版 */
@Composable
internal fun EdgeBlurBox(
    backgroundColor: Color,
    modifier: Modifier = Modifier,
    edgeHeight: Dp = 24.dp,
    content: @Composable () -> Unit,
) {
    Box(modifier = modifier) {
        content()

        Box(
            modifier = Modifier
                .fillMaxWidth()
                .height(edgeHeight)
                .align(Alignment.TopCenter)
                .background(
                    Brush.verticalGradient(
                        colors = listOf(
                            backgroundColor.copy(alpha = 0.95f),
                            backgroundColor.copy(alpha = 0.35f),
                            Color.Transparent,
                        )
                    )
                )
        )

        Box(
            modifier = Modifier
                .fillMaxWidth()
                .height(edgeHeight)
                .align(Alignment.BottomCenter)
                .background(
                    Brush.verticalGradient(
                        colors = listOf(
                            Color.Transparent,
                            backgroundColor.copy(alpha = 0.35f),
                            backgroundColor.copy(alpha = 0.95f),
                        )
                    )
                )
        )
    }
}

/**
 * 单一纯白卡片容器。
 *
 * **所有元素框统一纯白底、不描边**：底色就是 `Color.White`，不做半透明（92% 白会透出
 * 页面底色，读起来像叠了第二层）；边界交给阴影表达，不再往白卡上压别的颜色的框。
 * `borderColor` 仅作为逃生口保留，默认透明 —— 调用方不要再传彩色边框。
 */
@Composable
internal fun GlassCard(
    modifier: Modifier = Modifier,
    borderColor: Color = Color.Transparent,
    containerColor: Color = Color.White,
    shape: RoundedCornerShape = RoundedCornerShape(20.dp),
    onClick: (() -> Unit)? = null,
    content: @Composable ColumnScope.() -> Unit,
) {
    // 点按放大：与底部 Dock 的「duang」同一套手感 —— 按住浮起、松手过冲回弹。
    // 只挂在 onClick != null 的卡片上：不可点击的卡放大等于骗人（看着能点，点了没反应）。
    val interaction = remember { MutableInteractionSource() }
    val pressed by interaction.collectIsPressedAsState()
    val pressScale by animateFloatAsState(
        targetValue = if (pressed) 1.03f else 1f,
        // MediumBouncy：松手时越过 1f 再收回来，那一下回弹就是「放大动效」的本体，
        // 单纯的线性缩放只是「变大」，没有手感。
        animationSpec = spring(
            dampingRatio = Spring.DampingRatioMediumBouncy,
            stiffness = Spring.StiffnessMediumLow,
        ),
        label = "glass-card-press-scale",
    )
    val pressElevation by animateDpAsState(
        targetValue = if (pressed) 8.dp else 1.dp,
        animationSpec = spring(stiffness = Spring.StiffnessMediumLow),
        label = "glass-card-press-elevation",
    )

    val cardModifier = modifier
        .scale(pressScale)
        .then(
            if (onClick != null) {
                Modifier.clickable(
                    interactionSource = interaction,
                    // 不要涟漪：缩放 + 抬升本身就是反馈，再叠一层水波纹会打散那一下。
                    indication = null,
                    onClick = onClick,
                )
            } else {
                Modifier
            }
        )
    Card(
        modifier = cardModifier,
        shape = shape,
        colors = CardDefaults.cardColors(containerColor = containerColor),
        border = BorderStroke(1.dp, borderColor),
        elevation = CardDefaults.cardElevation(defaultElevation = pressElevation),
    ) {
        Column(Modifier.padding(16.dp), content = content)
    }
}

/** 页面标题 + 副标题 + 可选主色条 */
@Composable
internal fun ScreenHeader(
    title: String,
    subtitle: String,
    accentColor: Color,
) {
    Column {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Box(
                Modifier
                    .size(width = 4.dp, height = 20.dp)
                    .clip(RoundedCornerShape(3.dp))
                    .background(accentColor)
            )
            Spacer(Modifier.width(9.dp))
            Text(title, style = MaterialTheme.typography.titleLarge)
        }
        Spacer(Modifier.height(5.dp))
        Text(subtitle, color = DatappMuted, fontSize = 13.sp)
    }
}

@Composable
internal fun SectionHeader(title: String, action: String, accentColor: Color, onClick: () -> Unit) {
    Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
        Text(
            title,
            style = MaterialTheme.typography.titleMedium,
            modifier = Modifier.weight(1f),
            maxLines = 1,
            overflow = TextOverflow.Ellipsis,
        )
        TextButton(onClick = onClick, contentPadding = PaddingValues(0.dp)) {
            Text(action, color = accentColor, fontSize = 12.sp, fontWeight = FontWeight.SemiBold)
        }
    }
}

@Composable
internal fun MetricCard(
    title: String,
    value: String,
    sub: String,
    accent: Color,
    modifier: Modifier = Modifier,
) {
    GlassCard(modifier = modifier, shape = RoundedCornerShape(20.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Box(Modifier.size(8.dp).clip(CircleShape).background(accent))
            Spacer(Modifier.width(7.dp))
            Text(title, color = DatappMuted, fontSize = 11.sp, maxLines = 1, overflow = TextOverflow.Ellipsis)
        }
        Spacer(Modifier.height(10.dp))
        Text(value, color = DatappInk, fontSize = 28.sp, fontWeight = FontWeight.Bold)
        Spacer(Modifier.height(2.dp))
        Text(sub, color = DatappMuted, fontSize = 11.sp, maxLines = 1, overflow = TextOverflow.Ellipsis)
    }
}

@Composable
internal fun BackHeader(
    title: String,
    accentColor: Color,
    onBack: () -> Unit,
    trailing: (@Composable () -> Unit)? = null,
) {
    Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.fillMaxWidth()) {
        TextButton(onClick = onBack, contentPadding = PaddingValues(0.dp)) {
            Text("‹  返回", color = accentColor, fontSize = 13.sp, fontWeight = FontWeight.SemiBold)
        }
        Spacer(Modifier.width(8.dp))
        Text(title, style = MaterialTheme.typography.titleMedium, modifier = Modifier.weight(1f), maxLines = 1, overflow = TextOverflow.Ellipsis)
        trailing?.invoke()
    }
}

// ---------------------------------------------------------------- 结果可视化小件

/**
 * 置信度横条。
 *
 * 替掉满屏的「置信度 80%」纯文字：数字要换算才比得出高低，横条一眼就能看出
 * 「爆点模式 A 比 B 站得住」这类相对关系 —— 而相对关系才是读结论时真正要的东西。
 *
 * value 在模型层已 clamp 过 0..1，这里再兜一次：服务端若给了越界值，
 * fillMaxWidth 会直接抛异常崩掉整页。
 */
@Composable
internal fun ConfidenceBar(
    value: Float,
    color: Color,
    modifier: Modifier = Modifier,
    label: String? = null,
) {
    val v = value.coerceIn(0f, 1f)
    Column(modifier) {
        if (label != null) {
            Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                Text(label, color = DatappMuted, fontSize = 10.5.sp, modifier = Modifier.weight(1f))
                Text(
                    "${(v * 100).toInt()}%",
                    color = color,
                    fontSize = 10.5.sp,
                    fontWeight = FontWeight.SemiBold,
                )
            }
            Spacer(Modifier.height(4.dp))
        }
        Box(
            Modifier
                .fillMaxWidth()
                .height(5.dp)
                .clip(RoundedCornerShape(3.dp))
                .background(color.copy(alpha = 0.15f))
        ) {
            Box(
                Modifier
                    .fillMaxWidth(v)
                    .height(5.dp)
                    .clip(RoundedCornerShape(3.dp))
                    .background(color)
            )
        }
    }
}

/** 「• + 文本」要点列表 —— 分析归因 / 改进建议 / 局限都用它，别再各抄一遍 */
@Composable
internal fun BulletList(
    items: List<String>,
    accentColor: Color,
    modifier: Modifier = Modifier,
    textColor: Color = Color(0xFF4A5A6D),
) {
    Column(modifier) {
        items.forEach { item ->
            Row(Modifier.padding(top = 3.dp), verticalAlignment = Alignment.Top) {
                Text("•", color = accentColor, fontSize = 13.sp, fontWeight = FontWeight.Bold)
                Spacer(Modifier.width(8.dp))
                Text(item, color = textColor, fontSize = 12.sp, lineHeight = 19.sp)
            }
        }
    }
}

/**
 * 默认截断到 [collapsedLines] 行、点击展开/收起的正文。
 *
 * 检索命中与引用原文都可能很长，铺满屏幕就把结论淹了；但它们是证据链本身（与「无据拒答」
 * 那条红线同源），不能直接砍。折中是收着显示、点开看全。
 *
 * 「展开」只在**真的被截断**时才出现 —— 靠 `onTextLayout` 的 `hasVisualOverflow` 判断，
 * 而不是按字数估算：等宽字体不同、中英文混排差异大，估出来的阈值必然有误判。
 * 展开后不再更新该标志，否则一展开按钮就自己消失了。
 */
@Composable
internal fun ExpandableText(
    text: String,
    color: Color,
    accentColor: Color,
    modifier: Modifier = Modifier,
    collapsedLines: Int = 3,
) {
    var expanded by remember { mutableStateOf(false) }
    var overflowed by remember { mutableStateOf(false) }

    Column(modifier.clickable { expanded = !expanded }) {
        Text(
            text,
            color = color,
            fontSize = 12.sp,
            lineHeight = 19.sp,
            maxLines = if (expanded) Int.MAX_VALUE else collapsedLines,
            overflow = TextOverflow.Ellipsis,
            onTextLayout = { if (!expanded) overflowed = it.hasVisualOverflow },
        )
        if (overflowed) {
            Spacer(Modifier.height(3.dp))
            Text(
                if (expanded) "收起" else "展开",
                color = accentColor,
                fontSize = 10.5.sp,
                fontWeight = FontWeight.SemiBold,
            )
        }
    }
}

/** 一组 StatusChip 自动换行 —— 钩子 / 人群 / 话题 / 标签走它 */
@OptIn(ExperimentalLayoutApi::class)
@Composable
internal fun ChipRow(
    items: List<String>,
    color: Color,
    modifier: Modifier = Modifier,
) {
    val shown = items.filter { it.isNotBlank() }
    if (shown.isEmpty()) return
    FlowRow(
        modifier = modifier,
        horizontalArrangement = Arrangement.spacedBy(6.dp),
        verticalArrangement = Arrangement.spacedBy(6.dp),
    ) {
        shown.forEach { StatusChip(it, color) }
    }
}

/**
 * 默认收起的「数据存证」卡。
 *
 * app 是结果消费端：报告 ID / 内容 ID / 版本号对读结论毫无帮助，铺在正文里就是噪音。
 * 但它们也不能删 —— 结论看着不对劲时，得能拿 ID 回服务端查证。
 * 折中是默认收起、点开即见，正文保持干净。
 */
@Composable
internal fun CollapsibleCard(
    title: String,
    modifier: Modifier = Modifier,
    content: @Composable ColumnScope.() -> Unit,
) {
    var expanded by remember { mutableStateOf(false) }
    GlassCard(modifier = modifier.fillMaxWidth(), shape = RoundedCornerShape(16.dp)) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .clickable { expanded = !expanded },
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text(
                title,
                color = DatappMuted,
                fontSize = 11.5.sp,
                fontWeight = FontWeight.SemiBold,
                modifier = Modifier.weight(1f),
            )
            Text(if (expanded) "⌃" else "⌄", color = DatappMuted, fontSize = 13.sp)
        }
        Column(Modifier.animateContentSize()) {
            if (expanded) {
                Spacer(Modifier.height(9.dp))
                HorizontalDivider(color = DatappLine)
                Spacer(Modifier.height(9.dp))
                content()
            }
        }
    }
}

/** 存证卡里的一行「键 值」—— 键够短才配得起这个位置 */
@Composable
internal fun AttestRow(label: String, value: String) {
    Row(Modifier.padding(vertical = 2.dp), verticalAlignment = Alignment.Top) {
        Text(label, color = DatappMuted, fontSize = 11.sp, modifier = Modifier.width(64.dp))
        Text(value, color = Color(0xFF55667A), fontSize = 11.sp, lineHeight = 16.sp)
    }
}

// ---------------------------------------------------------------- 状态小件

/** 圆角小标签 —— 审核状态 / 来源 / 置信度都走它 */
@Composable
internal fun StatusChip(
    text: String,
    color: Color,
    modifier: Modifier = Modifier,
) {
    Surface(
        modifier = modifier,
        shape = RoundedCornerShape(7.dp),
        color = color.copy(alpha = 0.13f),
        border = BorderStroke(1.dp, color.copy(alpha = 0.25f)),
    ) {
        Text(
            text = text,
            color = color,
            fontSize = 10.sp,
            fontWeight = FontWeight.SemiBold,
            modifier = Modifier.padding(horizontal = 8.dp, vertical = 3.dp),
        )
    }
}

/** 审核状态 → 语义色（文案由调用方给，跟随 Content.reviewStatusLabel） */
internal fun reviewStatusColor(status: String): Color = when (status) {
    "approved" -> StatusApproved
    "extracted" -> StatusPending
    "rejected" -> StatusRejected
    else -> StatusUncertain
}

/** 断言状态 → 语义色 */
internal fun claimStatusColor(status: String): Color = when (status) {
    "supported" -> StatusApproved
    "contradicted" -> StatusRejected
    "unclear" -> StatusPending
    else -> StatusUncertain
}

/** 采集申请状态 → 语义色（文案由调用方给，跟随 CollectionRequest.statusLabel） */
internal fun requestStatusColor(status: String): Color = when (status) {
    "approved" -> StatusApproved
    "pending" -> StatusPending
    "rejected" -> StatusRejected
    else -> StatusUncertain
}

/**
 * 证据来源机码 → 中文（服务端 `EvidenceKind`：content / platform / external / human）。
 *
 * 这是审核面板上最该看懂的一个标签 —— 「这条证据是拿内容自己推的，还是平台上的
 * 旁证」，直接决定断言可信度。露 `platform` 这种机码等于让用户自己翻译。
 */
internal fun sourceKindLabel(kind: String): String = when (kind) {
    "content" -> "本内容"
    "platform" -> "平台数据"
    "external" -> "外部来源"
    "human" -> "人工判断"
    else -> kind.ifBlank { "来源" }
}

/** 知识库文档状态 → 语义色 */
internal fun kbStatusColor(status: String): Color = when (status) {
    "ready" -> StatusApproved
    "embedding" -> StatusPending
    "failed" -> StatusRejected
    else -> StatusUncertain
}

// ---------------------------------------------------------------- 空态 / 错误 / 加载

@Composable
internal fun EmptyCard(title: String, subtitle: String) {
    GlassCard(modifier = Modifier.fillMaxWidth(), shape = RoundedCornerShape(18.dp)) {
        Column(
            modifier = Modifier.fillMaxWidth().padding(vertical = 10.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            Text("◌", color = DatappMuted, fontSize = 26.sp)
            Spacer(Modifier.height(6.dp))
            Text(title, color = DatappInk, fontSize = 14.sp, fontWeight = FontWeight.SemiBold)
            Spacer(Modifier.height(4.dp))
            Text(subtitle, color = DatappMuted, fontSize = 12.sp)
        }
    }
}

/**
 * 错误横幅。**必须显示服务端原文** —— 静默吞掉错误就等于伪造成功，
 * 与项目红线冲突（见 CLAUDE.md「不伪造成功」）。
 */
@Composable
internal fun ErrorBanner(message: String, modifier: Modifier = Modifier) {
    Surface(
        modifier = modifier.fillMaxWidth(),
        shape = RoundedCornerShape(14.dp),
        color = StatusRejected.copy(alpha = 0.08f),
        border = BorderStroke(1.dp, StatusRejected.copy(alpha = 0.3f)),
    ) {
        Row(Modifier.padding(horizontal = 14.dp, vertical = 11.dp), verticalAlignment = Alignment.Top) {
            Text("!", color = StatusRejected, fontSize = 14.sp, fontWeight = FontWeight.Bold)
            Spacer(Modifier.width(9.dp))
            Text(message, color = Color(0xFF8A3A44), fontSize = 12.sp, lineHeight = 18.sp)
        }
    }
}

/** 简洁加载态（不定进度条式的转圈，用文字 + 主色点） */
@Composable
internal fun LoadingRow(text: String, accentColor: Color, modifier: Modifier = Modifier) {
    Row(
        modifier = modifier.fillMaxWidth().padding(vertical = 14.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.Center,
    ) {
        Box(Modifier.size(7.dp).clip(CircleShape).background(accentColor))
        Spacer(Modifier.width(9.dp))
        Text(text, color = DatappMuted, fontSize = 12.sp)
    }
}

// ---------------------------------------------------------------- 行内富文本

/**
 * 行内富文本：把模型正文里的 markdown 标记**渲染掉**，而不是原样显示。
 *
 * 与已删除的 `MarkdownBlock` 的关键区别是**不认 `#` 标题**。
 * 标题意味着「这是一篇文档」—— 结果页不该有文档结构，分节由卡片的 SectionHeader
 * 承担；而模型正文里若真冒出 `#`，那多半是它跑偏了，当普通文本显示比当标题供起来诚实。
 *
 * 只保留两类真正影响阅读的标记：
 *   - `**加粗**` → 真加粗。旧实现是 `replace("**", "")` 把标记**删掉**，
 *     「**开头钩子**」显示成「开头钩子」却毫无强调，等于把语义丢了
 *   - `- ` / `* ` 行首 → `•` 列表项
 *
 * 不引第三方 markdown 库，也不解析 HTML：每行都当纯文本走 AnnotatedString，
 * 所以不存在「模型输出被当代码执行」的问题。
 */
@Composable
internal fun RichText(text: String, accentColor: Color, modifier: Modifier = Modifier) {
    Column(modifier) {
        text.lines().forEach { raw ->
            val line = raw.trimEnd()
            when {
                line.isBlank() -> Spacer(Modifier.height(8.dp))

                line.startsWith("- ") || line.startsWith("* ") -> Row(
                    Modifier.padding(start = 2.dp, top = 3.dp),
                    verticalAlignment = Alignment.Top,
                ) {
                    Text("•", color = accentColor, fontSize = 13.sp, fontWeight = FontWeight.Bold)
                    Spacer(Modifier.width(8.dp))
                    Text(
                        annotatedEmphasis(line.substring(2)),
                        color = Color(0xFF4A5A6D),
                        fontSize = 13.sp,
                        lineHeight = 20.sp,
                    )
                }

                else -> Text(
                    annotatedEmphasis(line),
                    color = Color(0xFF4A5A6D),
                    fontSize = 13.sp,
                    lineHeight = 20.sp,
                    modifier = Modifier.padding(top = 2.dp),
                )
            }
        }
    }
}

/**
 * `**…**` → 真加粗；`` `…` `` 只脱掉反引号（行内代码在 app 上没有等宽字体语境，
 * 再描个边框只会更吵）。
 *
 * 按 `**` 切分后**奇数段**才是被包裹的内容。用下标判断而不是正则替换，
 * 是为了让「模型给了不成对的 `**`」这类脏输入退化成纯文本，而不是吃掉半行字。
 */
private fun annotatedEmphasis(text: String): AnnotatedString = buildAnnotatedString {
    text.split("**").forEachIndexed { i, part ->
        val clean = part.replace("`", "")
        if (i % 2 == 1) {
            withStyle(SpanStyle(fontWeight = FontWeight.Bold, color = DatappInk)) { append(clean) }
        } else {
            append(clean)
        }
    }
}
