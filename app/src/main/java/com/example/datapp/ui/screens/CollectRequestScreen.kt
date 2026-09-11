package com.example.datapp.ui.screens

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.FilterChip
import androidx.compose.material3.FilterChipDefaults
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.example.datapp.data.ApiResult
import com.example.datapp.data.CollectionRequest
import com.example.datapp.data.DatappRepository
import com.example.datapp.data.errorMessage
import com.example.datapp.data.isUnauthorized
import com.example.datapp.ui.components.BackHeader
import com.example.datapp.ui.components.EmptyCard
import com.example.datapp.ui.components.ErrorBanner
import com.example.datapp.ui.components.GlassCard
import com.example.datapp.ui.components.LoadingRow
import com.example.datapp.ui.components.StatusChip
import com.example.datapp.ui.components.requestStatusColor
import com.example.datapp.ui.theme.DatappInk
import com.example.datapp.ui.theme.DatappMuted
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/**
 * 采集检索：app 端提交关键词 → 管理员在 web 看板放行 → 后台走 OpenCLI 用浏览器登录态
 * 搜索 → 结果入库 → 回到内容池（双端读同一个库，同步是天然的）。
 *
 * **这里提交的只是「意图」** —— 纯写库、零外部网络，不会立刻访问平台。放行权在管理员
 * 手里，因为设备令牌编在 APK 里可被反编译，服务端对它的决策端点一律 401。
 * 页面顶部那句话必须显式写出来，否则用户会以为点了就在采集。
 */
@Composable
fun CollectRequestScreen(
    repository: DatappRepository,
    accentColor: Color,
    onBack: () -> Unit,
    onSessionExpired: () -> Unit,
) {
    val api = repository.apiClient
    val scope = rememberCoroutineScope()

    var keyword by rememberSaveable { mutableStateOf("") }
    var maxItems by rememberSaveable { mutableStateOf(10) }
    var requests by remember { mutableStateOf(emptyList<CollectionRequest>()) }
    var loading by remember { mutableStateOf(true) }
    var error by remember { mutableStateOf<String?>(null) }
    var submitting by remember { mutableStateOf(false) }
    var notice by remember { mutableStateOf<String?>(null) }
    var noticeOk by remember { mutableStateOf(true) }

    suspend fun load() {
        loading = true
        when (val r = withContext(Dispatchers.IO) { api.listCollectionRequests() }) {
            is ApiResult.Success -> {
                requests = r.data.let { arr ->
                    (0 until arr.length()).mapNotNull { i ->
                        arr.optJSONObject(i)?.let { CollectionRequest.from(it) }
                    }
                }
                error = null
            }
            is ApiResult.Error -> if (r.isUnauthorized) {
                onSessionExpired(); return
            } else {
                error = r.message
            }
        }
        loading = false
    }

    LaunchedEffect(Unit) { load() }

    fun submit() {
        val q = keyword.trim()
        if (q.isEmpty() || submitting) return
        submitting = true
        notice = null
        scope.launch {
            val result = withContext(Dispatchers.IO) { api.submitCollectionRequest(q, maxItems) }
            submitting = false
            when (result) {
                is ApiResult.Success -> {
                    keyword = ""
                    noticeOk = true
                    notice = "已提交，等待管理员在 web 看板放行"
                    load()
                }
                is ApiResult.Error -> {
                    if (result.isUnauthorized) { onSessionExpired(); return@launch }
                    noticeOk = false
                    notice = result.message
                }
            }
        }
    }

    val options = listOf(1, 5, 10)

    LazyColumn(
        modifier = Modifier.fillMaxSize(),
        contentPadding = PaddingValues(start = 20.dp, end = 20.dp, top = 18.dp, bottom = 96.dp),
        verticalArrangement = Arrangement.spacedBy(14.dp),
    ) {
        item {
            BackHeader(title = "采集检索", accentColor = accentColor, onBack = onBack)
        }

        // 这句话是页面的一部分，不是装饰：不写清楚，用户会把「提交」当成「开始采集」。
        item {
            GlassCard(shape = RoundedCornerShape(18.dp)) {
                Text(
                    "提交后需管理员在 web 看板放行，才会真正访问平台采集。",
                    color = DatappInk,
                    fontSize = 12.5.sp,
                    lineHeight = 19.sp,
                )
                Spacer(Modifier.height(6.dp))
                Text(
                    "采集走桌面端已登录的浏览器会话，低频少量；结果会直接出现在内容池里。",
                    color = DatappMuted,
                    fontSize = 11.5.sp,
                    lineHeight = 17.sp,
                )
            }
        }

        item {
            Column {
                OutlinedTextField(
                    value = keyword,
                    onValueChange = { keyword = it },
                    modifier = Modifier.fillMaxWidth(),
                    singleLine = true,
                    shape = RoundedCornerShape(18.dp),
                    placeholder = { Text("输入检索关键词，如：露营装备", color = DatappMuted, fontSize = 13.sp) },
                    leadingIcon = { Text("⌕", color = accentColor, fontSize = 22.sp) },
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Text),
                    colors = OutlinedTextFieldDefaults.colors(
                        focusedContainerColor = Color.White.copy(alpha = 0.9f),
                        unfocusedContainerColor = Color.White.copy(alpha = 0.85f),
                        focusedBorderColor = accentColor.copy(alpha = 0.6f),
                        unfocusedBorderColor = Color.White,
                    ),
                )
                Spacer(Modifier.height(10.dp))
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text("条数上限", color = DatappMuted, fontSize = 12.sp)
                    Spacer(Modifier.width(10.dp))
                    options.forEach { n ->
                        FilterChip(
                            selected = maxItems == n,
                            onClick = { maxItems = n },
                            label = {
                                Text(
                                    "$n",
                                    fontSize = 12.sp,
                                    color = if (maxItems == n) accentColor else DatappMuted,
                                    fontWeight = if (maxItems == n) FontWeight.SemiBold else FontWeight.Normal,
                                )
                            },
                            colors = FilterChipDefaults.filterChipColors(
                                selectedContainerColor = accentColor.copy(alpha = 0.12f),
                                containerColor = Color.White.copy(alpha = 0.7f),
                            ),
                            border = FilterChipDefaults.filterChipBorder(
                                enabled = true,
                                selected = maxItems == n,
                                selectedBorderColor = accentColor.copy(alpha = 0.3f),
                                borderColor = Color.White,
                            ),
                            modifier = Modifier.padding(end = 6.dp),
                        )
                    }
                }
                Spacer(Modifier.height(10.dp))
                Button(
                    onClick = { submit() },
                    enabled = keyword.isNotBlank() && !submitting,
                    modifier = Modifier.fillMaxWidth(),
                    shape = RoundedCornerShape(16.dp),
                    colors = ButtonDefaults.buttonColors(
                        containerColor = accentColor,
                        contentColor = Color.White,
                    ),
                ) {
                    Text(
                        if (submitting) "提交中…" else "提交采集申请",
                        fontSize = 14.sp,
                        fontWeight = FontWeight.SemiBold,
                    )
                }
            }
        }

        if (notice != null) {
            item {
                if (noticeOk) {
                    GlassCard(shape = RoundedCornerShape(14.dp)) {
                        Text(notice!!, color = accentColor, fontSize = 12.5.sp, fontWeight = FontWeight.SemiBold)
                    }
                } else {
                    ErrorBanner(notice!!)
                }
            }
        }
        if (error != null) item { ErrorBanner(error!!) }

        item {
            Text(
                "我的申请",
                color = DatappInk,
                fontSize = 14.sp,
                fontWeight = FontWeight.Bold,
                modifier = Modifier.padding(top = 4.dp),
            )
        }

        when {
            loading && requests.isEmpty() -> item { LoadingRow("正在加载申请记录…", accentColor) }
            requests.isEmpty() -> item { EmptyCard("还没有提交过采集申请", "在上面输入关键词试试") }
            else -> items(requests, key = { it.id }) { req ->
                GlassCard(modifier = Modifier.fillMaxWidth(), shape = RoundedCornerShape(18.dp)) {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Column(Modifier.weight(1f)) {
                            Text(
                                req.target,
                                color = DatappInk,
                                fontSize = 14.sp,
                                fontWeight = FontWeight.Bold,
                            )
                            Spacer(Modifier.height(4.dp))
                            Text(
                                "最多 ${req.maxItems} 条 · ${req.createdAt}",
                                color = DatappMuted,
                                fontSize = 11.sp,
                            )
                        }
                        StatusChip(req.statusLabel, requestStatusColor(req.status))
                    }
                    if (req.note != null) {
                        Spacer(Modifier.height(8.dp))
                        Text("驳回理由：${req.note}", color = DatappMuted, fontSize = 11.5.sp)
                    }
                }
            }
        }
    }
}
