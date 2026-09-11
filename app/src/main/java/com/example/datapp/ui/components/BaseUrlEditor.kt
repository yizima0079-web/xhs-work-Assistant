package com.example.datapp.ui.components

import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.example.datapp.BuildConfig
import com.example.datapp.data.ApiResult
import com.example.datapp.data.DatappApiClient
import com.example.datapp.data.errorMessage
import com.example.datapp.data.normalizeBaseUrl
import com.example.datapp.ui.theme.DatappMuted
import com.example.datapp.ui.theme.StatusApproved
import com.example.datapp.ui.theme.StatusRejected
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.async
import kotlinx.coroutines.awaitAll
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.launch

/** 探测结果，只用来驱动对话框里的那行提示。 */
private sealed interface ProbeUi {
    data object Idle : ProbeUi
    data object Busy : ProbeUi
    data class Done(val ok: Boolean, val text: String) : ProbeUi
}

/** 探测产出：命中则 [url] 非空；全不中则 [error] 是第一条候选的失败原因。 */
private data class ProbeOutcome(val url: String?, val error: String?)

/**
 * 后端地址编辑器。
 *
 * **「我的」页与登录页共用同一个对话框**，两处各写一套的话，登录页那套迟早被漏掉。
 * 登录页那处不是可有可无的：Cookie 模式下地址若是错的，冷启动必然停在登录页，
 * 而「我的」页要会话就绪之后才可达 —— 没有这个入口，用户会被**锁死在登录页**，
 * 连能改地址的地方都找不到。
 */
@Composable
internal fun BaseUrlDialog(
    current: String,
    accentColor: Color,
    onDismiss: () -> Unit,
    onSave: (String) -> Unit,
) {
    var draft by remember { mutableStateOf(current) }
    var probe by remember { mutableStateOf<ProbeUi>(ProbeUi.Idle) }
    val scope = rememberCoroutineScope()

    // 探测一律用临时 client，**绝不改正在生效的那个** —— 否则测一个坏地址会顺手
    // 把当前会话打挂，用户连「取消」都救不回来。
    fun runProbe(targets: List<String>, onHit: (String) -> Unit) {
        scope.launch {
            probe = ProbeUi.Busy
            val outcome = probeFirstReachable(targets)
            val hit = outcome.url
            if (hit != null) {
                onHit(hit)
                probe = ProbeUi.Done(true, "可达：$hit")
            } else {
                probe = ProbeUi.Done(false, "连不上。${outcome.error}")
            }
        }
    }

    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("后端地址", fontSize = 16.sp, fontWeight = FontWeight.Bold) },
        text = {
            Column {
                Text(
                    "地址只存在这台设备上，重启 app 依然生效，不用改 gradle.properties 重编。",
                    color = DatappMuted,
                    fontSize = 11.5.sp,
                    lineHeight = 17.sp,
                )
                Spacer(Modifier.height(12.dp))
                OutlinedTextField(
                    value = draft,
                    onValueChange = {
                        draft = it
                        probe = ProbeUi.Idle
                    },
                    modifier = Modifier.fillMaxWidth(),
                    singleLine = true,
                    placeholder = {
                        Text("http://192.168.0.109:8000", color = DatappMuted, fontSize = 13.sp)
                    },
                    colors = OutlinedTextFieldDefaults.colors(
                        // 对话框本身已经是一种底色，输入框不再铺第二层白 —— 只留边框做边界。
                        // 原先白底 + 白边落在浅色对话框上，就会读成「框里又嵌了个白框」。
                        focusedContainerColor = Color.Transparent,
                        unfocusedContainerColor = Color.Transparent,
                        focusedBorderColor = accentColor.copy(alpha = 0.6f),
                        unfocusedBorderColor = accentColor.copy(alpha = 0.25f),
                    ),
                )
                Spacer(Modifier.height(4.dp))
                Row(verticalAlignment = Alignment.CenterVertically) {
                    val busy = probe is ProbeUi.Busy
                    TextButton(
                        onClick = {
                            val target = normalizeBaseUrl(draft)
                            if (target == null) {
                                probe = ProbeUi.Done(false, "地址格式不对，检查一下：$draft")
                            } else {
                                runProbe(listOf(target)) { }
                            }
                        },
                        enabled = !busy,
                        contentPadding = PaddingValues(horizontal = 6.dp),
                    ) {
                        Text("测试连接", color = accentColor, fontSize = 12.sp, fontWeight = FontWeight.SemiBold)
                    }
                    TextButton(
                        onClick = { runProbe(probeCandidates(draft, current)) { draft = it } },
                        enabled = !busy,
                        contentPadding = PaddingValues(horizontal = 6.dp),
                    ) {
                        Text("自动探测", color = accentColor, fontSize = 12.sp, fontWeight = FontWeight.SemiBold)
                    }
                }
                Spacer(Modifier.height(2.dp))
                when (val p = probe) {
                    ProbeUi.Idle -> Unit
                    ProbeUi.Busy -> Row(verticalAlignment = Alignment.CenterVertically) {
                        CircularProgressIndicator(
                            modifier = Modifier.size(13.dp),
                            strokeWidth = 1.6.dp,
                            color = accentColor,
                        )
                        Spacer(Modifier.width(7.dp))
                        Text("探测中…", color = DatappMuted, fontSize = 11.5.sp)
                    }
                    is ProbeUi.Done -> Text(
                        // 错误原文照实显示，不美化也不吞掉 —— 与 ErrorBanner 同一条约定。
                        text = p.text,
                        color = if (p.ok) StatusApproved else StatusRejected,
                        fontSize = 11.5.sp,
                        lineHeight = 17.sp,
                    )
                }
            }
        },
        confirmButton = {
            TextButton(onClick = {
                val normalized = normalizeBaseUrl(draft)
                if (normalized == null) {
                    probe = ProbeUi.Done(false, "地址格式不对，检查一下：$draft")
                } else {
                    onSave(normalized)
                }
            }) {
                Text("保存", color = accentColor, fontSize = 13.sp, fontWeight = FontWeight.Bold)
            }
        },
        dismissButton = {
            TextButton(onClick = onDismiss) {
                Text("取消", color = DatappMuted, fontSize = 13.sp)
            }
        },
    )
}

/**
 * 候选地址：用户当前输入的、**打开对话框时正在生效的**、编译期兜底，
 * 以及两个「环境自带」的固定地址。
 *
 * 第二个候选（[current] = `repository.baseUrl`）不是冗余的：用户手滑把地址改坏之后
 * 才想起来点「自动探测」，此时唯一还能救命的恰恰是那个正在生效的旧地址。
 * 只探输入框里那个的话，候选里全是坏的，探测必然全灭。
 *
 * `127.0.0.1` 是为 `adb reverse tcp:8000 tcp:8000` 准备的 —— 配了它，模拟器和
 * USB 真机都能用一条不碰网络栈的通路，换网、防火墙、代理全都不影响。
 * `10.0.2.2` 是模拟器指向宿主机的标准别名（**本机实测不通**，见 gradle.properties
 * 的踩坑记录），换台机器可能通，留着不亏 —— 反正并发探，多一个不增加耗时。
 *
 * 注意这里**不做网段扫描**（用户已明确排除）：DHCP 若是换到别的网段，这份候选表
 * 一个都命中不了，此时只能手填。别把「自动探测」当万能。
 */
private fun probeCandidates(input: String, current: String): List<String> = buildList {
    add(input)
    add(current)
    add(BuildConfig.DATAPP_BASE_URL)
    add("http://10.0.2.2:8000")
    add("http://127.0.0.1:8000")
}.mapNotNull(::normalizeBaseUrl).distinct()

/**
 * **并发**探完所有候选，再按候选顺序取第一个可达的。
 *
 * 刻意不用「谁先返回用谁」：那样结果会随网络抖动变化，同一个候选列表两次点出不同地址。
 *
 * 全并发是因为串行会把最坏耗时叠成「候选数 × 超时」，4 个候选 × 2.5s = 10s，
 * 用户点一下等十秒已经算卡死了。
 */
private suspend fun probeFirstReachable(candidates: List<String>): ProbeOutcome = coroutineScope {
    val results = candidates
        .map { url -> async(Dispatchers.IO) { url to DatappApiClient(url).health() } }
        .awaitAll()
    results.firstOrNull { (_, result) -> result is ApiResult.Success }
        ?.let { ProbeOutcome(it.first, null) }
        ?: ProbeOutcome(null, results.firstOrNull()?.second?.errorMessage ?: "没有可探测的地址")
}
