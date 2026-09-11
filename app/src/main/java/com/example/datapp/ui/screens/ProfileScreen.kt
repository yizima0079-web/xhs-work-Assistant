package com.example.datapp.ui.screens

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.example.datapp.data.DatappRepository
import com.example.datapp.data.SummaryStats
import com.example.datapp.ui.components.BaseUrlDialog
import com.example.datapp.ui.components.EdgeBlurBox
import com.example.datapp.ui.components.GlassCard
import com.example.datapp.ui.components.ScreenHeader
import com.example.datapp.ui.theme.DatappCyan
import com.example.datapp.ui.theme.DatappInk
import com.example.datapp.ui.theme.DatappLine
import com.example.datapp.ui.theme.DatappMuted
import com.example.datapp.ui.theme.StatusRejected

/**
 * 我的：登录态 + 服务端可达性 + 数据概览 + 登出。
 *
 * 这里不放假开关 —— 「每日洞察提醒」「数据来源标记」那类本地开关不产生任何真实行为，
 * 放在设置页只会让人以为它生效了。只展示能验证的事实。
 */
@Composable
fun ProfileScreen(
    username: String,
    repository: DatappRepository,
    stats: SummaryStats,
    reportCount: Int,
    kbDocumentCount: Int,
    accentColor: Color,
    bgColor: Color,
    onLogout: () -> Unit,
    onSaveBaseUrl: (String) -> Unit,
    /** 设备令牌模式下为 false：没有会话可退，「退出登录」按钮点了也没有意义。 */
    canLogout: Boolean = true,
) {
    var editingBaseUrl by remember { mutableStateOf(false) }

    EdgeBlurBox(backgroundColor = bgColor) {
        LazyColumn(
            contentPadding = PaddingValues(start = 20.dp, end = 20.dp, top = 20.dp, bottom = 96.dp),
            verticalArrangement = Arrangement.spacedBy(16.dp),
        ) {
            item {
                ScreenHeader(
                    title = "我的",
                    subtitle = "登录态、服务端连接与数据概览",
                    accentColor = accentColor,
                )
            }

            item {
                GlassCard(shape = RoundedCornerShape(22.dp)) {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Box(
                            Modifier
                                .size(50.dp)
                                .clip(CircleShape)
                                .background(Brush.linearGradient(listOf(accentColor, DatappCyan))),
                            contentAlignment = Alignment.Center,
                        ) {
                            Text(
                                text = username.firstOrNull()?.uppercase() ?: "?",
                                color = Color.White,
                                fontSize = 22.sp,
                                fontWeight = FontWeight.Bold,
                            )
                        }
                        Spacer(Modifier.width(13.dp))
                        Column {
                            Text(
                                text = username.ifBlank { "未登录" },
                                color = DatappInk,
                                fontSize = 16.sp,
                                fontWeight = FontWeight.SemiBold,
                            )
                            Spacer(Modifier.height(2.dp))
                            Text("管理员会话 · 可读写审核 / 分析 / 报告 / 知识库", color = DatappMuted, fontSize = 12.sp)
                        }
                    }
                }
            }

            item {
                Text("数据概览", style = MaterialTheme.typography.titleMedium)
                Spacer(Modifier.height(8.dp))
                GlassCard(shape = RoundedCornerShape(20.dp)) {
                    ProfileRow("内容池", "${stats.totalContentsCount} 条")
                    HorizontalDivider(color = DatappLine, modifier = Modifier.padding(vertical = 10.dp))
                    ProfileRow("待处理 / 可信", "${stats.pendingReviewCount} / ${stats.approvedCount}")
                    HorizontalDivider(color = DatappLine, modifier = Modifier.padding(vertical = 10.dp))
                    ProfileRow("报告", "$reportCount 份")
                    HorizontalDivider(color = DatappLine, modifier = Modifier.padding(vertical = 10.dp))
                    ProfileRow("知识库文档", "$kbDocumentCount 份")
                }
            }

            item {
                Text("连接", style = MaterialTheme.typography.titleMedium)
                Spacer(Modifier.height(8.dp))
                GlassCard(shape = RoundedCornerShape(20.dp)) {
                    Column {
                        Text("后端地址", color = DatappMuted, fontSize = 11.sp)
                        Spacer(Modifier.height(3.dp))
                        Text(repository.baseUrl, color = DatappInk, fontSize = 12.5.sp, fontWeight = FontWeight.SemiBold)
                        Spacer(Modifier.height(8.dp))
                        Text(
                            "地址存在这台设备上，重启 app 依然生效。换网络导致电脑 IP 变了，改这里就行，不用重编。",
                            color = DatappMuted,
                            fontSize = 11.sp,
                            lineHeight = 17.sp,
                        )
                        Spacer(Modifier.height(12.dp))
                        Button(
                            onClick = { editingBaseUrl = true },
                            modifier = Modifier.fillMaxWidth().height(40.dp),
                            shape = RoundedCornerShape(13.dp),
                            // 透明底：卡片本身已经是白的，按钮再铺一层白就是一个「白框里的白框」
                            colors = ButtonDefaults.buttonColors(containerColor = Color.Transparent),
                            border = androidx.compose.foundation.BorderStroke(1.dp, accentColor.copy(alpha = 0.4f)),
                        ) {
                            Text("修改地址", color = accentColor, fontSize = 12.5.sp, fontWeight = FontWeight.Bold)
                        }
                    }
                }
                if (editingBaseUrl) {
                    BaseUrlDialog(
                        current = repository.baseUrl,
                        accentColor = accentColor,
                        onDismiss = { editingBaseUrl = false },
                        onSave = {
                            editingBaseUrl = false
                            onSaveBaseUrl(it)
                        },
                    )
                }
            }

            item {
                Text("会话", style = MaterialTheme.typography.titleMedium)
                Spacer(Modifier.height(8.dp))
                GlassCard(shape = RoundedCornerShape(20.dp)) {
                    Text(
                        if (canLogout) {
                            "会话由服务端签发，默认 12 小时过期；过期后任意请求返回 401，会立刻回到登录页。"
                        } else {
                            "本机已配置设备令牌，开箱即用、无需账号密码。" +
                                "令牌编在安装包里，如需吊销请在服务端更换 DATAPP_APP_TOKEN 后重装。"
                        },
                        color = DatappMuted,
                        fontSize = 11.5.sp,
                        lineHeight = 18.sp,
                    )
                    // 设备令牌模式下不渲染登出按钮：没有会话可退，点了也不会真的登出
                    // （下个请求照样带令牌放行），留着只会误导。
                    if (canLogout) {
                        Spacer(Modifier.height(14.dp))
                        Button(
                            onClick = onLogout,
                            modifier = Modifier.fillMaxWidth().height(44.dp),
                            shape = RoundedCornerShape(13.dp),
                            colors = ButtonDefaults.buttonColors(containerColor = Color.Transparent),
                            border = androidx.compose.foundation.BorderStroke(1.dp, StatusRejected.copy(alpha = 0.4f)),
                        ) {
                            Text("退出登录", color = StatusRejected, fontSize = 13.sp, fontWeight = FontWeight.Bold)
                        }
                    }
                }
            }

            item {
                Text(
                    "版本 0.3.0 · 内容 / 知识库全功能接入",
                    color = DatappMuted,
                    fontSize = 11.sp,
                    modifier = Modifier.padding(top = 6.dp),
                )
            }
        }
    }
}

@Composable
private fun ProfileRow(label: String, value: String) {
    Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
        Text(label, color = DatappInk, fontSize = 13.5.sp, modifier = Modifier.weight(1f))
        Text(value, color = DatappInk, fontSize = 13.5.sp, fontWeight = FontWeight.SemiBold)
    }
}
