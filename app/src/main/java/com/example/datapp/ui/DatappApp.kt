package com.example.datapp.ui

import androidx.activity.compose.BackHandler
import androidx.compose.animation.core.Animatable
import androidx.compose.animation.core.Spring
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.spring
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Surface
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
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.scale
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.IntOffset
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.example.datapp.BuildConfig
import com.example.datapp.data.ApiResult
import com.example.datapp.data.Content
import com.example.datapp.data.DatappRepository
import com.example.datapp.data.KbDocument
import com.example.datapp.data.ReportDetail
import com.example.datapp.data.SessionStore
import com.example.datapp.ui.components.LoadingRow
import com.example.datapp.ui.screens.CollectRequestScreen
import com.example.datapp.ui.screens.ContentDetailScreen
import com.example.datapp.ui.screens.ContentScreen
import com.example.datapp.ui.screens.HomeScreen
import com.example.datapp.ui.screens.KnowledgeScreen
import com.example.datapp.ui.screens.LoginScreen
import com.example.datapp.ui.screens.ProfileScreen
import com.example.datapp.ui.screens.ReportDetailScreen
import com.example.datapp.ui.theme.DatappMuted
import com.example.datapp.ui.theme.ThemeContentAccent
import com.example.datapp.ui.theme.ThemeContentBg
import com.example.datapp.ui.theme.ThemeHomeAccent
import com.example.datapp.ui.theme.ThemeHomeBg
import com.example.datapp.ui.theme.ThemeKnowledgeAccent
import com.example.datapp.ui.theme.ThemeKnowledgeBg
import com.example.datapp.ui.theme.ThemeProfileAccent
import com.example.datapp.ui.theme.ThemeProfileBg
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

private enum class AppTab(
    val label: String,
    val symbol: String,
    val accentColor: Color,
    val backgroundColor: Color,
) {
    Home("首页", "⌂", ThemeHomeAccent, ThemeHomeBg),
    Content("内容", "⌁", ThemeContentAccent, ThemeContentBg),
    Knowledge("知识库", "▤", ThemeKnowledgeAccent, ThemeKnowledgeBg),
    Profile("我的", "○", ThemeProfileAccent, ThemeProfileBg),
}

private enum class SessionPhase { Probing, NeedLogin, Ready }

@Composable
fun DatappApp(
    repository: DatappRepository = remember { DatappRepository() },
) {
    val context = LocalContext.current
    val store = remember { SessionStore(context) }
    val scope = rememberCoroutineScope()

    var phase by remember { mutableStateOf(SessionPhase.Probing) }
    var username by remember { mutableStateOf("admin") }
    var lastUsername by remember { mutableStateOf("") }
    // 启动判定的「代次」。改后端地址等于换了一个后端，必须把整套判定重跑一遍，
    // 而 LaunchedEffect 只跑一次 —— 递增这个 key 就是重跑的开关。
    var probeKey by remember { mutableStateOf(0) }

    var selectedTab by rememberSaveable { mutableStateOf(AppTab.Home.name) }
    var openedContentId by rememberSaveable { mutableStateOf("") }
    var openedReportId by rememberSaveable { mutableStateOf("") }
    var openedCollect by rememberSaveable { mutableStateOf(false) }

    var contents by remember { mutableStateOf<List<Content>>(emptyList()) }
    var reports by remember { mutableStateOf<List<ReportDetail>>(emptyList()) }
    var kbDocuments by remember { mutableStateOf<List<KbDocument>>(emptyList()) }

    var loading by remember { mutableStateOf(false) }
    var contentsError by remember { mutableStateOf<String?>(null) }
    var kbError by remember { mutableStateOf<String?>(null) }

    suspend fun syncAll() {
        loading = true
        var cErr: String? = null
        var rErr: String? = null
        var dErr: String? = null

        val cJob = scope.launch(Dispatchers.IO) {
            val (cList, err) = repository.fetchContents()
            withContext(Dispatchers.Main) {
                contents = cList
                cErr = err
            }
        }
        val rJob = scope.launch(Dispatchers.IO) {
            val (rList, err) = repository.fetchReports()
            withContext(Dispatchers.Main) {
                reports = rList
                rErr = err
            }
        }
        val dJob = scope.launch(Dispatchers.IO) {
            val (dList, err) = repository.fetchKbDocuments()
            withContext(Dispatchers.Main) {
                kbDocuments = dList
                dErr = err
            }
        }

        cJob.join()
        rJob.join()
        dJob.join()

        contentsError = cErr ?: rErr
        kbError = dErr
        loading = false
    }

    fun forgetSession() {
        store.cookie = null
        repository.apiClient.restoreSession(null)
        contents = emptyList()
        reports = emptyList()
        kbDocuments = emptyList()
        openedContentId = ""
        openedReportId = ""
        openedCollect = false
        selectedTab = AppTab.Home.name
        phase = SessionPhase.NeedLogin
    }

    /**
     * 换后端地址。**换地址 = 换后端 = 换数据库**：旧 Cookie 的签名对新后端多半无效，
     * 带过去只会先刷出一屏 401 再回落登录页，看起来像「又坏了」。所以一并清掉，
     * 让下面的启动判定自己决定进主界面还是回登录页。
     *
     * 这里不直接改 [phase] 就完事 —— 探测逻辑只写在 [LaunchedEffect] 里，
     * 递增 `probeKey` 触发它重跑，免得同一套判定散成两份、迟早不一致。
     */
    fun switchBaseUrl(saved: String) {
        store.baseUrl = saved
        repository.apiClient.baseUrl = saved
        store.cookie = null
        repository.apiClient.restoreSession(null)
        contents = emptyList()
        reports = emptyList()
        kbDocuments = emptyList()
        openedContentId = ""
        openedReportId = ""
        openedCollect = false
        selectedTab = AppTab.Home.name
        phase = SessionPhase.Probing
        probeKey++
    }

    LaunchedEffect(probeKey) {
        // **必须早于下面任何一次请求** —— 用户存的地址若生效不了，
        // 后面全是拿编译期默认地址打出去的，白跑一轮 401。
        store.baseUrl?.let { repository.apiClient.baseUrl = it }

        // app 通道：设备令牌已配置 → **免账号密码**直进主界面，连探测都不用发。
        // 令牌为空（没配 / 配错）→ 走下面的 Cookie 探测，失败回落登录页。
        // 登录页与整套三态机**刻意保留**：兜底一旦删掉，令牌配错就等于 app 变砖。
        if (BuildConfig.DATAPP_APP_TOKEN.isNotEmpty()) {
            username = "admin"
            phase = SessionPhase.Ready
            syncAll()
            return@LaunchedEffect
        }

        lastUsername = store.lastUsername.orEmpty()
        val saved = store.cookie
        if (saved.isNullOrBlank()) {
            phase = SessionPhase.NeedLogin
            return@LaunchedEffect
        }
        repository.apiClient.restoreSession(saved)
        when (val probe = withContext(Dispatchers.IO) { repository.session() }) {
            is ApiResult.Success -> {
                username = probe.data.optString("username").ifBlank { "admin" }
                phase = SessionPhase.Ready
                syncAll()
            }
            is ApiResult.Error -> forgetSession()
        }
    }

    when (phase) {
        SessionPhase.Probing -> Box(
            modifier = Modifier.fillMaxSize().background(ThemeHomeBg),
            contentAlignment = Alignment.Center,
        ) {
            LoadingRow("正在检查登录状态…", ThemeHomeAccent)
        }

        SessionPhase.NeedLogin -> LoginScreen(
            repository = repository,
            initialUsername = lastUsername,
            onSaveBaseUrl = ::switchBaseUrl,
            onLoggedIn = { name ->
                store.cookie = repository.apiClient.sessionCookie
                store.lastUsername = name
                username = name
                phase = SessionPhase.Ready
                scope.launch { syncAll() }
            },
        )

        SessionPhase.Ready -> {
            val tab = AppTab.entries.find { it.name == selectedTab } ?: AppTab.Home
            val openedContent = contents.firstOrNull { it.contentId == openedContentId }
            val hasOverlay = openedContent != null || openedReportId.isNotBlank() || openedCollect

            BackHandler(enabled = hasOverlay) {
                openedContentId = ""
                openedReportId = ""
                openedCollect = false
            }

            Box(
                modifier = Modifier
                    .fillMaxSize()
                    .background(tab.backgroundColor)
            ) {
                Box(modifier = Modifier.fillMaxSize()) {
                    when {
                        openedCollect -> CollectRequestScreen(
                            repository = repository,
                            accentColor = tab.accentColor,
                            onBack = { openedCollect = false },
                            onSessionExpired = ::forgetSession,
                        )

                        openedReportId.isNotBlank() -> ReportDetailScreen(
                            reportId = openedReportId,
                            repository = repository,
                            accentColor = tab.accentColor,
                            onBack = { openedReportId = "" },
                        )

                        openedContent != null -> ContentDetailScreen(
                            content = openedContent,
                            coverUrl = repository.absoluteCover(openedContent),
                            repository = repository,
                            accentColor = tab.accentColor,
                            onBack = { openedContentId = "" },
                            onOpenReport = { openedReportId = it },
                            onSessionExpired = ::forgetSession,
                            onContentChanged = { scope.launch { syncAll() } },
                        )

                        tab == AppTab.Home -> HomeScreen(
                            username = username,
                            contents = contents,
                            reports = reports,
                            kbDocumentCount = kbDocuments.size,
                            stats = repository.summarize(contents),
                            coverOf = { repository.absoluteCover(it) },
                            accentColor = tab.accentColor,
                            bgColor = tab.backgroundColor,
                            loading = loading,
                            error = contentsError,
                            onContent = { openedContentId = it },
                            onReport = { openedReportId = it },
                            onGoContent = { selectedTab = AppTab.Content.name },
                            onGoKnowledge = { selectedTab = AppTab.Knowledge.name },
                            onRefresh = { syncAll() },
                        )

                        tab == AppTab.Content -> ContentScreen(
                            contents = contents,
                            coverOf = { repository.absoluteCover(it) },
                            accentColor = tab.accentColor,
                            bgColor = tab.backgroundColor,
                            loading = loading,
                            error = contentsError,
                            onContent = { openedContentId = it },
                            onCollect = { openedCollect = true },
                            onRefresh = { scope.launch { syncAll() } },
                        )

                        tab == AppTab.Knowledge -> KnowledgeScreen(
                            repository = repository,
                            documents = kbDocuments,
                            documentsLoading = loading,
                            documentsError = kbError,
                            onRefreshDocuments = { syncAll() },
                            accentColor = tab.accentColor,
                            bgColor = tab.backgroundColor,
                            onSessionExpired = ::forgetSession,
                        )

                        else -> ProfileScreen(
                            username = username,
                            repository = repository,
                            stats = repository.summarize(contents),
                            reportCount = reports.size,
                            kbDocumentCount = kbDocuments.size,
                            accentColor = tab.accentColor,
                            bgColor = tab.backgroundColor,
                            onLogout = {
                                scope.launch {
                                    withContext(Dispatchers.IO) { repository.logout() }
                                    forgetSession()
                                }
                            },
                            onSaveBaseUrl = ::switchBaseUrl,
                            // 设备令牌模式下没有会话可退，隐藏登出按钮。
                            canLogout = BuildConfig.DATAPP_APP_TOKEN.isEmpty(),
                        )
                    }
                }

                // [类 macOS MacBook Dock 悬浮毛玻璃 BottomBar，带 Q 弹 "duang" 放大与跳跃动效]
                Box(
                    modifier = Modifier
                        .align(Alignment.BottomCenter)
                        .fillMaxWidth()
                        .navigationBarsPadding()
                        .padding(horizontal = 20.dp, vertical = 10.dp)
                ) {
                    Surface(
                        modifier = Modifier
                            .fillMaxWidth()
                            .height(76.dp),
                        shape = RoundedCornerShape(38.dp),
                        color = Color.White.copy(alpha = 0.82f),
                        shadowElevation = 10.dp,
                    ) {
                        Row(
                            modifier = Modifier
                                .fillMaxSize()
                                .padding(horizontal = 6.dp),
                            horizontalArrangement = Arrangement.SpaceAround,
                            verticalAlignment = Alignment.CenterVertically,
                        ) {
                            AppTab.entries.forEach { item ->
                                val isSelected = tab == item
                                val activeColor = item.accentColor

                                // 1. MacBook Dock 风格 Q 弹 "duang" —— 点击触发的一次性脉冲。
                                //
                                // 这里必须用 Animatable 而不是 animateFloatAsState：
                                // 后者是「跟随状态」，选中即停在放大值上不再回落，
                                // 而且重复点同一个入口值没变化 → 一点反馈都没有，出不来那一下。
                                // Animatable 由点击直接驱动：冲高 → 过冲回弹 → 归位。
                                val pulse = remember { Animatable(0f) }
                                val clickSource = remember { MutableInteractionSource() }

                                // 2. macOS Dock 指示圆点动画
                                val dotScale by animateFloatAsState(
                                    targetValue = if (isSelected) 1.0f else 0f,
                                    animationSpec = spring(
                                        dampingRatio = Spring.DampingRatioMediumBouncy,
                                        stiffness = Spring.StiffnessMedium,
                                    ),
                                    label = "dock-dot-scale",
                                )

                                Column(
                                    modifier = Modifier
                                        // 圆角正方形：固定尺寸，不再靠内边距撑成扁矩形。
                                        // 58dp 是量出来的：内容（图标行高+标签行高+圆点）实测约 50dp，
                                        // 52dp 会顶穿，圆点被 .clip() 切掉 —— 方块必须比内容高出一档。
                                        .size(58.dp)
                                        .scale(1f + pulse.value + if (isSelected) 0.05f else 0f)
                                        .offset { IntOffset(0, (-pulse.value * 12f).dp.roundToPx()) }
                                        .clip(RoundedCornerShape(19.dp))
                                        .background(
                                            if (isSelected) activeColor.copy(alpha = 0.14f)
                                            else Color.Transparent
                                        )
                                        .clickable(
                                            interactionSource = clickSource,
                                            indication = null,   // 苹果风不要水波纹，靠缩放给反馈
                                        ) {
                                            selectedTab = item.name
                                            openedContentId = ""
                                            openedReportId = ""
                                            openedCollect = false
                                            scope.launch {
                                                pulse.snapTo(0f)
                                                pulse.animateTo(
                                                    0.30f,   // 峰值 1.30 倍，配下面的强过冲
                                                    spring(
                                                        dampingRatio = 0.30f,
                                                        stiffness = Spring.StiffnessHigh,
                                                    ),
                                                )
                                                pulse.animateTo(
                                                    0f,
                                                    spring(
                                                        dampingRatio = 0.55f,
                                                        stiffness = Spring.StiffnessMediumLow,
                                                    ),
                                                )
                                            }
                                        },
                                    horizontalAlignment = Alignment.CenterHorizontally,
                                    verticalArrangement = Arrangement.Center,
                                ) {
                                    Text(
                                        text = item.symbol,
                                        fontSize = 20.sp,
                                        color = if (isSelected) activeColor else DatappMuted,
                                        fontWeight = if (isSelected) FontWeight.Bold else FontWeight.Normal,
                                    )

                                    Spacer(Modifier.height(1.dp))

                                    Text(
                                        text = item.label,
                                        fontSize = 10.5.sp,
                                        color = if (isSelected) activeColor else DatappMuted,
                                        fontWeight = if (isSelected) FontWeight.Bold else FontWeight.Normal,
                                    )

                                    // macOS Dock 活跃指示小圆点
                                    Box(
                                        modifier = Modifier
                                            .padding(top = 3.dp)
                                            .size(4.dp)
                                            .scale(dotScale)
                                            .clip(CircleShape)
                                            .background(activeColor)
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
