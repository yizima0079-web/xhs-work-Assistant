package com.example.datapp.data

import com.example.datapp.BuildConfig
import org.json.JSONArray
import org.json.JSONObject
import java.io.BufferedReader
import java.io.InputStreamReader
import java.io.OutputStreamWriter
import java.net.HttpURLConnection
import java.net.URL
import java.net.URLEncoder

/**
 * Android 端后端 API 通讯客户端
 * 契约规范依据：docs/app端对接契约.md
 *
 * 认证两条通道（详见契约 §1.3）：
 *  · 移动端设备令牌（`X-Datapp-App-Token`）—— 免账号密码启动，可读可写（不含采集模块）
 *  · 管理员 Cookie（`datapp_admin_session`）—— 登录页登录后由服务端下发，经 [applyAuth] 自动携带
 */
class DatappApiClient(
    // 默认取编译期注入的 BuildConfig.DATAPP_BASE_URL（源头是 gradle.properties 的
    // datapp.baseUrl）。绝不硬编码 127.0.0.1：真机上那指向手机自己，请求永远连不到后端。
    var baseUrl: String = BuildConfig.DATAPP_BASE_URL,
) {
    var sessionCookie: String? = null
        private set

    /** 是否已持有管理员会话（仅表示本地存了 Cookie，真实有效性以服务端响应为准） */
    val isLoggedIn: Boolean get() = sessionCookie != null

    /**
     * 用本地持久化的 Cookie 恢复会话（app 重启后免重复登录）。
     * 只是「先假定有效」，真正是否有效由下一次请求的 401 决定。
     */
    fun restoreSession(cookie: String?) {
        sessionCookie = cookie?.takeIf { it.isNotBlank() }
    }

    // ---------------------------------------------------------------- 探活

    /**
     * 服务端存活探针，供地址编辑器校验「用户填的这个地址上是否真的活着一个后端」。
     *
     * **必须用根路径 `/health`，不要改成 `/api/v1/health`**：后者会 spawn 一个 OpenCLI
     * 子进程做真实登录态探测 —— 冷缓存实测 8.9s、偶发超过 30s，且没装采集工具时直接 500。
     * 拿它做探针，会把「服务好得很，只是探针慢 / 没装采集工具」误判成「这个地址不通」。
     * `/health` 是纯内存、免认证、毫秒级。这一点在 server/README.md 有记录。
     *
     * 超时用 [TIMEOUT_PROBE] 而非默认值：这是交互式探测，用户点一下就要有反馈，
     * 不能挂在 8 秒的连接超时上。
     */
    fun health(): ApiResult<String> =
        request("GET", "/health", readTimeoutMs = TIMEOUT_PROBE, connectTimeoutMs = TIMEOUT_PROBE)

    // ---------------------------------------------------------------- 认证

    /**
     * 管理员登录。成功时保存 Set-Cookie，后续写操作自动携带。
     *
     * 密码由调用方（登录页）传入 —— 不在代码里写死任何默认密码。
     */
    fun login(username: String, password: String): ApiResult<Boolean> {
        val body = JSONObject().apply {
            put("username", username)
            put("password", password)
        }
        return try {
            val conn = openConnection("POST", "/api/v1/auth/login")
            conn.doOutput = true
            conn.setRequestProperty("Content-Type", "application/json; charset=UTF-8")
            OutputStreamWriter(conn.outputStream, "UTF-8").use { writer ->
                writer.write(body.toString())
                writer.flush()
            }
            val statusCode = conn.responseCode
            if (statusCode == 200) {
                val cookies = conn.headerFields["Set-Cookie"]
                if (!cookies.isNullOrEmpty()) {
                    sessionCookie = cookies.firstOrNull()?.split(";")?.get(0)
                }
                ApiResult.Success(true)
            } else {
                ApiResult.Error(readStream(conn.errorStream).ifBlank { "账号或密码错误" }, statusCode)
            }
        } catch (e: Exception) {
            ApiResult.Error("登录异常: ${e.localizedMessage}", -1, e)
        }
    }

    /** 销会话并清掉本地 Cookie */
    fun logout(): ApiResult<String> {
        val result = request("POST", "/api/v1/auth/logout")
        sessionCookie = null
        return result
    }

    /** 探测当前会话是否有效（带 Cookie 请求，401 即失效） */
    fun session(): ApiResult<JSONObject> = get("/api/v1/auth/session").map { JSONObject(it) }

    // ---------------------------------------------------------------- 内容池

    /** 内容池列表 (GET /api/v1/contents) */
    fun getContents(limit: Int = 20): ApiResult<JSONArray> =
        get("/api/v1/contents?limit=$limit").map { JSONArray(it) }

    /** 内容派生计数 (GET /api/v1/contents/summary) */
    fun getContentSummaries(limit: Int = 20): ApiResult<JSONArray> =
        get("/api/v1/contents/summary?limit=$limit").map { JSONArray(it) }

    /** 单条内容详情 (GET /api/v1/contents/{id}) */
    fun getContentDetail(contentId: String): ApiResult<JSONObject> =
        get("/api/v1/contents/${enc(contentId)}").map { JSONObject(it) }

    /** 删除内容（写） (DELETE /api/v1/contents/{id}) */
    fun deleteContent(contentId: String): ApiResult<String> =
        request("DELETE", "/api/v1/contents/${enc(contentId)}")

    /**
     * 「删除」= app 端隐藏（写） (POST /api/v1/contents/{id}/app-hidden)
     *
     * **不是真删**：服务端只打一个 `app_hidden_at` 标记，内容与它的断言/分析/
     * 报告/知识库文档全部原样保留，web 看板照常可见、可一键恢复。
     *
     * 与 `deleteContent` 的区别不止在语义 —— 这个端点返回 204 且**设备令牌可以调**；
     * 反过来「取消隐藏」（DELETE 同一路径）被服务端对设备令牌 401，
     * 恢复只能由管理端做。
     */
    fun hideContent(contentId: String): ApiResult<String> =
        request("POST", "/api/v1/contents/${enc(contentId)}/app-hidden")

    // ---------------------------------------------------------------- 分析

    /** 某内容的分析版本列表 (GET /api/v1/contents/{id}/analyses) */
    fun getAnalysesForContent(contentId: String, limit: Int = 50): ApiResult<JSONArray> =
        get("/api/v1/contents/${enc(contentId)}/analyses?limit=$limit").map { JSONArray(it) }

    /** 单条分析 (GET /api/v1/analyses/{id}) */
    fun getAnalysis(analysisId: String): ApiResult<JSONObject> =
        get("/api/v1/analyses/${enc(analysisId)}").map { JSONObject(it) }

    /**
     * 触发分析（写，新增一版，历史保留） (POST /api/v1/contents/{id}/analyze)
     * 走 LLM，超时按 LLM 档。
     */
    fun analyzeContent(contentId: String, focus: String = ""): ApiResult<JSONObject> {
        val body = JSONObject().apply { put("focus", focus) }
        return request("POST", "/api/v1/contents/${enc(contentId)}/analyze", body, TIMEOUT_READ_LLM)
            .map { JSONObject(it) }
    }

    // ---------------------------------------------------------------- 审核

    /**
     * 某内容的审核详情：断言 + 证据 + 全部批次
     * (GET /api/v1/contents/{id}/claims?batch_id=)
     */
    fun getClaims(contentId: String, batchId: String? = null): ApiResult<JSONObject> {
        val q = if (batchId.isNullOrBlank()) "" else "?batch_id=${enc(batchId)}"
        return get("/api/v1/contents/${enc(contentId)}/claims$q").map { JSONObject(it) }
    }

    /** 审核批次列表 (GET /api/v1/contents/{id}/review-batches) */
    fun getReviewBatches(contentId: String): ApiResult<JSONArray> =
        get("/api/v1/contents/${enc(contentId)}/review-batches").map { JSONArray(it) }

    /**
     * 触发自动审核（写） (POST /api/v1/contents/{id}/review?force=)
     * force=true 为「重新审核」，新增一版批次，旧版保留。
     */
    fun reviewContent(contentId: String, force: Boolean = false): ApiResult<JSONObject> {
        val q = if (force) "?force=true" else ""
        return request("POST", "/api/v1/contents/${enc(contentId)}/review$q", null, TIMEOUT_READ_LLM)
            .map { JSONObject(it) }
    }

    /**
     * 人工改判单条断言（写） (POST /api/v1/claims/{id}/decision)
     * status: unverified | supported | contradicted | unclear
     */
    fun decideClaim(
        claimId: String,
        status: String,
        rationale: String = "客户端人工复核",
    ): ApiResult<JSONObject> {
        val body = JSONObject().apply {
            put("status", status)
            put("rationale", rationale)
            put("evidence_ids", JSONArray())
        }
        return request("POST", "/api/v1/claims/${enc(claimId)}/decision", body).map { JSONObject(it) }
    }

    /** 切换生效批次（写） (POST /api/v1/contents/{id}/review-batches/{bid}/activate) */
    fun activateBatch(contentId: String, batchId: String): ApiResult<JSONObject> =
        request("POST", "/api/v1/contents/${enc(contentId)}/review-batches/${enc(batchId)}/activate")
            .map { JSONObject(it) }

    // ---------------------------------------------------------------- 报告

    /** 报告列表 (GET /api/v1/reports)，contentId 非空则只看该内容的 */
    fun getReports(limit: Int = 50, contentId: String? = null): ApiResult<JSONArray> {
        val q = if (contentId.isNullOrBlank()) "?limit=$limit" else "?limit=$limit&content_id=${enc(contentId)}"
        return get("/api/v1/reports$q").map { JSONArray(it) }
    }

    /** 单份报告 (GET /api/v1/reports/{id}) */
    fun getReportDetail(reportId: String): ApiResult<JSONObject> =
        get("/api/v1/reports/${enc(reportId)}").map { JSONObject(it) }

    /** 生成报告（写，走 LLM） (POST /api/v1/reports) */
    fun createReport(contentIds: List<String>, title: String = "审核报告"): ApiResult<JSONObject> {
        val body = JSONObject().apply {
            put("content_ids", JSONArray(contentIds))
            put("title", title)
        }
        return request("POST", "/api/v1/reports", body, TIMEOUT_READ_LLM).map { JSONObject(it) }
    }

    // ---------------------------------------------------------------- 知识库

    /** 知识库文档列表 (GET /api/v1/kb/documents) */
    fun getKbDocuments(limit: Int = 100): ApiResult<JSONArray> =
        get("/api/v1/kb/documents?limit=$limit").map { JSONArray(it) }

    /** 单份知识库文档 (GET /api/v1/kb/documents/{id}) */
    fun getKbDocument(docId: String): ApiResult<JSONObject> =
        get("/api/v1/kb/documents/${enc(docId)}").map { JSONObject(it) }

    /** 向量化单份文档（写，走向量模型） (POST /api/v1/kb/documents/{id}/vectorize) */
    fun vectorizeDocument(docId: String): ApiResult<JSONObject> =
        request("POST", "/api/v1/kb/documents/${enc(docId)}/vectorize", null, TIMEOUT_READ_LLM)
            .map { JSONObject(it) }

    /** 批量向量化（写） (POST /api/v1/kb/documents/vectorize) */
    fun vectorizeDocuments(docIds: List<String>): ApiResult<JSONArray> {
        val body = JSONObject().apply { put("doc_ids", JSONArray(docIds)) }
        return request("POST", "/api/v1/kb/documents/vectorize", body, TIMEOUT_READ_LLM)
            .map { JSONArray(it) }
    }

    /** 删除文档（写） (DELETE /api/v1/kb/documents/{id}) */
    fun deleteKbDocument(docId: String): ApiResult<String> =
        request("DELETE", "/api/v1/kb/documents/${enc(docId)}")

    /**
     * 内容一步入库（写） (POST /api/v1/kb/contents/{id})
     * 服务端有红线门禁：源内容 review_status 必须为 approved，否则 409。
     */
    fun saveContentToKb(contentId: String): ApiResult<JSONObject> =
        request("POST", "/api/v1/kb/contents/${enc(contentId)}", null, TIMEOUT_READ_LLM)
            .map { JSONObject(it) }

    /** 分析一步入库（写） (POST /api/v1/kb/analyses/{id}) */
    fun saveAnalysisToKb(analysisId: String): ApiResult<JSONObject> =
        request("POST", "/api/v1/kb/analyses/${enc(analysisId)}", null, TIMEOUT_READ_LLM)
            .map { JSONObject(it) }

    /** 把分析蒸馏成 Q&A 知识（写，走 LLM） (POST /api/v1/kb/analyses/{id}/distill-qa?force=) */
    fun distillAnalysisQa(analysisId: String, force: Boolean = false): ApiResult<JSONObject> {
        val q = if (force) "?force=true" else ""
        return request("POST", "/api/v1/kb/analyses/${enc(analysisId)}/distill-qa$q", null, TIMEOUT_READ_LLM)
            .map { JSONObject(it) }
    }

    /** 语义检索 (POST /api/v1/kb/search) */
    fun kbSearch(query: String, topK: Int = 8): ApiResult<JSONArray> {
        val body = JSONObject().apply {
            put("query", query)
            put("top_k", topK)
        }
        return request("POST", "/api/v1/kb/search", body).map { JSONArray(it) }
    }

    /**
     * 知识问答 RAG (POST /api/v1/kb/ask)
     * history 最多 6 轮；无有效引用时服务端强制拒答（answered=false）。
     */
    fun kbAsk(query: String, topK: Int = 6, history: JSONArray? = null): ApiResult<JSONObject> {
        val body = JSONObject().apply {
            put("query", query)
            put("top_k", topK)
            if (history != null) put("history", history)
        }
        return request("POST", "/api/v1/kb/ask", body, TIMEOUT_READ_LLM).map { JSONObject(it) }
    }

    /** 某 Q&A 文档的问答对列表 (GET /api/v1/kb/qa/documents/{docId}/pairs) */
    fun getQaPairs(docId: String): ApiResult<JSONArray> =
        get("/api/v1/kb/qa/documents/${enc(docId)}/pairs").map { JSONArray(it) }

    /** 通过问答对（写） (POST /api/v1/kb/qa/pairs/approve) */
    fun approveQaPairs(qaIds: List<String>): ApiResult<JSONObject> {
        val body = JSONObject().apply { put("qa_ids", JSONArray(qaIds)) }
        return request("POST", "/api/v1/kb/qa/pairs/approve", body).map { JSONObject(it) }
    }

    /** 驳回问答对（写） (POST /api/v1/kb/qa/pairs/reject) */
    fun rejectQaPairs(qaIds: List<String>): ApiResult<JSONObject> {
        val body = JSONObject().apply { put("qa_ids", JSONArray(qaIds)) }
        return request("POST", "/api/v1/kb/qa/pairs/reject", body).map { JSONObject(it) }
    }

    // ---------------------------------------------------------------- 采集申请

    /**
     * 提交采集申请（写） (POST /api/v1/collection-requests)
     *
     * **这里不会触发任何采集** —— 只往待办表写一行 pending，零外部网络。真正的采集要等
     * 管理员在 web 看板放行。app 拿不到放行权是设计边界（设备令牌在服务端被前缀挡死），
     * 所以客户端**不存在**任何直接调用 `/collection-runs` 的入口，也不要加。
     */
    fun submitCollectionRequest(target: String, maxItems: Int = 10): ApiResult<JSONObject> {
        val body = JSONObject().apply {
            put("target", target)
            put("max_items", maxItems)
        }
        return request("POST", "/api/v1/collection-requests", body).map { JSONObject(it) }
    }

    /** 我的采集申请列表 (GET /api/v1/collection-requests)，待放行的排最前 */
    fun listCollectionRequests(limit: Int = 30): ApiResult<JSONArray> =
        get("/api/v1/collection-requests?limit=$limit").map { JSONArray(it) }

    // ---------------------------------------------------------------- 内部

    private fun enc(raw: String): String = URLEncoder.encode(raw, "UTF-8")

    private fun openConnection(
        method: String,
        endpoint: String,
        connectTimeoutMs: Int = TIMEOUT_CONNECT,
    ): HttpURLConnection {
        val conn = URL("$baseUrl$endpoint").openConnection() as HttpURLConnection
        conn.requestMethod = method
        conn.connectTimeout = connectTimeoutMs
        conn.readTimeout = TIMEOUT_READ
        applyAuth(conn)
        return conn
    }

    /**
     * 通用请求。body 非空则以 JSON 发送。
     *
     * [readTimeoutMs] 单独可调：分析 / 审核 / 报告 / 问答 / 蒸馏都走 LLM，
     * 服务端 DATAPP_LLM_TIMEOUT=300，客户端必须留出余量，否则长推理必被读超时掐断。
     */
    private fun request(
        method: String,
        endpoint: String,
        body: JSONObject? = null,
        readTimeoutMs: Int = TIMEOUT_READ,
        connectTimeoutMs: Int = TIMEOUT_CONNECT,
    ): ApiResult<String> {
        return try {
            val conn = openConnection(method, endpoint, connectTimeoutMs)
            conn.readTimeout = readTimeoutMs
            if (body != null) {
                conn.doOutput = true
                conn.setRequestProperty("Content-Type", "application/json; charset=UTF-8")
                OutputStreamWriter(conn.outputStream, "UTF-8").use { writer ->
                    writer.write(body.toString())
                    writer.flush()
                }
            }
            val statusCode = conn.responseCode
            if (statusCode in 200..299) {
                ApiResult.Success(readStream(conn.inputStream))
            } else {
                val detail = readStream(conn.errorStream).ifBlank { "无响应内容" }
                ApiResult.Error("$method $endpoint 失败 (HTTP $statusCode): $detail", statusCode)
            }
        } catch (e: Exception) {
            ApiResult.Error("$method $endpoint 异常: ${e.localizedMessage}", -1, e)
        }
    }

    private fun get(endpoint: String): ApiResult<String> = request("GET", endpoint)

    /**
     * 统一挂认证信息。两个头**同时挂**（不是互斥的优先/回落），服务端任一通过即可。
     *
     * - `X-Datapp-App-Token`：移动端设备令牌，可读可写 —— 配了它就免账号密码，
     *   无需登录页；服务端对这个令牌关死了采集模块（app 本就不继承采集）。
     * - `Cookie`：管理员会话，登录页登录后才有，作为令牌缺失时的兜底。
     *
     * 两者都不存在时什么都不挂：服务端返回 401，失败显式暴露，不做静默降级。
     */
    private fun applyAuth(conn: HttpURLConnection) {
        if (BuildConfig.DATAPP_APP_TOKEN.isNotEmpty()) {
            conn.setRequestProperty("X-Datapp-App-Token", BuildConfig.DATAPP_APP_TOKEN)
        }
        sessionCookie?.let { conn.setRequestProperty("Cookie", it) }
    }

    private fun readStream(stream: java.io.InputStream?): String {
        if (stream == null) return ""
        return BufferedReader(InputStreamReader(stream, "UTF-8")).use { it.readText() }
    }

    private companion object {
        const val TIMEOUT_CONNECT = 8_000
        const val TIMEOUT_READ = 30_000

        /** 走 LLM 的端点：服务端超时 300s，客户端留 20s 余量。 */
        const val TIMEOUT_READ_LLM = 320_000

        /**
         * 地址探测（[health]）的连接与读超时。
         * 默认 8s 对交互式探测太长 —— 候选地址是**并发**探的，用户点完就得看到结果。
         */
        const val TIMEOUT_PROBE = 2_500
    }
}

sealed class ApiResult<out T> {
    data class Success<out T>(val data: T) : ApiResult<T>()
    data class Error(val message: String, val statusCode: Int = -1, val cause: Throwable? = null) : ApiResult<Nothing>()
}

/**
 * 把 [ApiResult] 的字符串载荷解析成目标类型。
 * 解析失败也归为 Error —— 不返回半个对象让上层误以为成功。
 */
internal fun <T> ApiResult<String>.map(parse: (String) -> T): ApiResult<T> = when (this) {
    is ApiResult.Success ->
        try {
            ApiResult.Success(parse(data))
        } catch (e: Exception) {
            ApiResult.Error("响应解析失败: ${e.localizedMessage}", -2, e)
        }
    is ApiResult.Error -> this
}

/** 会话失效判定：任意请求返回 401 即需重新登录 */
val ApiResult<*>.isUnauthorized: Boolean
    get() = this is ApiResult.Error && statusCode == 401

/** 取错误文案（成功时为 null） */
val ApiResult<*>.errorMessage: String?
    get() = (this as? ApiResult.Error)?.message

/**
 * 规范化用户手填的后端地址：合法返回规范化结果，非法返回 `null`。
 *
 * 三件事缺一不可，全是实际会踩的：
 *  · **补 scheme** —— 用户很少会打 `http://`，多半只敲 `192.168.0.109:8000`。
 *    不补的话 [URL] 会把 `192.168.0.109` 当成协议名，请求直接失败。
 *  · **去掉尾部所有 `/`** —— 所有 endpoint 都自带前导 `/`，留着尾斜杠会拼出
 *    `http://x:8000//api/v1/contents` 这种双斜杠路径。**但只能剥 scheme 之后的**：
 *    无脑 `trimEnd('/')` 会把光秃秃的 `http://` 啃成 `http:/`，补 scheme 后成了
 *    `http://http:/`（host 变成 `http`）—— 一个本该判非法的串反而变成「合法」地址。
 *  · **trim 前后空白** —— 从聊天窗口复制地址几乎必然带上尾随空格。
 *
 * `URL` 解析通过还不够：空 host 在 Java 里不一定抛异常，必须显式查 host。
 *
 * scheme 比对按小写做（`HTTP://x` 也算「已经带了 scheme」），否则会被补成
 * `http://HTTP://x` —— 手机输入法首字母自动大写是很常见的事。
 */
internal fun normalizeBaseUrl(raw: String): String? {
    val trimmed = raw.trim()
    if (trimmed.isBlank()) return null
    val hasScheme = trimmed.lowercase().let {
        it.startsWith("http://") || it.startsWith("https://")
    }
    val withScheme = if (hasScheme) trimmed else "http://$trimmed"

    // 以 "://" 为界切开，只在后半段剥尾斜杠，scheme 那两道斜杠碰不到。
    val sep = withScheme.indexOf("://")
    val candidate =
        if (sep < 0) withScheme
        else withScheme.substring(0, sep + 3) + withScheme.substring(sep + 3).trimEnd('/')

    return try {
        val url = URL(candidate)
        if (url.host.isNullOrBlank()) null else candidate
    } catch (_: Exception) {
        null
    }
}
