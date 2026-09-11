package com.example.datapp.data

import org.json.JSONObject

data class SummaryStats(
    val totalContentsCount: Int,
    val pendingReviewCount: Int,
    val approvedCount: Int,
    val rejectedCount: Int,
) {
    /** 已审核占比（%）。分母为 0 时返回 0，不编造数字。 */
    val reviewedRatio: Float
        get() = if (totalContentsCount == 0) 0f
        else (approvedCount + rejectedCount).toFloat() / totalContentsCount

    val reviewedRatioLabel: String get() = "${(reviewedRatio * 100).toInt()}%"
}

/**
 * 数据仓库：把服务端 JSON 映射成 [Models.kt] 里的契约模型。
 *
 * 只做映射与聚合，不做任何兜底假数据 —— 失败就把错误文案原样交给上层显示。
 * 写操作（审核 / 分析 / 报告 / 向量化 …）由页面直接调 [apiClient]，这一层不转手。
 */
class DatappRepository(
    val apiClient: DatappApiClient = DatappApiClient(),
) {
    val baseUrl: String get() = apiClient.baseUrl

    // ------------------------------------------------------------ 内容池

    fun fetchContents(limit: Int = 30): Pair<List<Content>, String?> =
        apiClient.getContents(limit).mapList { Content.from(it) }

    fun fetchContent(contentId: String): Pair<Content?, String?> =
        apiClient.getContentDetail(contentId).mapOne { Content.from(it) }

    /**
     * 封面绝对化：`cover_local`（/media/xx.webp，服务端自有，永不过期）优先；
     * 远程 `cover_url`（xhscdn 带时效签名）仅作兜底。见契约 §1.4。
     */
    fun absoluteCover(content: Content): String? {
        val local = content.coverLocal
        if (local.isNotBlank()) {
            val path = if (local.startsWith("/")) local else "/$local"
            return "$baseUrl$path"
        }
        val remote = content.coverUrl
        return when {
            remote.isBlank() -> null
            remote.startsWith("/media/") -> "$baseUrl$remote"
            else -> remote
        }
    }

    // ------------------------------------------------------------ 分析

    fun fetchAnalyses(contentId: String, limit: Int = 50): Pair<List<Analysis>, String?> =
        apiClient.getAnalysesForContent(contentId, limit).mapList { Analysis.from(it) }

    // ------------------------------------------------------------ 审核

    fun fetchReviewDetail(contentId: String, batchId: String? = null): Pair<ContentReviewDetail?, String?> =
        apiClient.getClaims(contentId, batchId).mapOne { ContentReviewDetail.from(it) }

    // ------------------------------------------------------------ 报告

    fun fetchReports(limit: Int = 50, contentId: String? = null): Pair<List<ReportDetail>, String?> =
        apiClient.getReports(limit, contentId).mapList { ReportDetail.from(it) }

    fun fetchReportDetail(reportId: String): Pair<ReportDetail?, String?> =
        apiClient.getReportDetail(reportId).mapOne { ReportDetail.from(it) }

    // ------------------------------------------------------------ 知识库

    fun fetchKbDocuments(limit: Int = 100): Pair<List<KbDocument>, String?> =
        apiClient.getKbDocuments(limit).mapList { KbDocument.from(it) }

    fun fetchQaPairs(docId: String): Pair<List<KbQaPair>, String?> =
        apiClient.getQaPairs(docId).mapList { KbQaPair.from(it) }

    // ------------------------------------------------------------ 概览统计

    /** KPI 全部由真实内容列表派生，不做任何放大或凑数。 */
    fun summarize(contents: List<Content>): SummaryStats = SummaryStats(
        totalContentsCount = contents.size,
        pendingReviewCount = contents.count { it.reviewStatus == "pending" || it.reviewStatus == "extracted" },
        approvedCount = contents.count { it.reviewStatus == "approved" },
        rejectedCount = contents.count { it.reviewStatus == "rejected" },
    )

    // ------------------------------------------------------------ 认证

    fun login(username: String, password: String): ApiResult<Boolean> = apiClient.login(username, password)

    fun logout(): ApiResult<String> = apiClient.logout()

    /** 会话探测：成功返回 username 等字段，401 表示需要重新登录 */
    fun session(): ApiResult<JSONObject> = apiClient.session()

    // ------------------------------------------------------------ 内部

    private fun <T> ApiResult<org.json.JSONArray>.mapList(parse: (JSONObject) -> T): Pair<List<T>, String?> =
        when (this) {
            is ApiResult.Success -> {
                val out = mutableListOf<T>()
                for (i in 0 until data.length()) {
                    data.optJSONObject(i)?.let { out.add(parse(it)) }
                }
                Pair(out, null)
            }
            is ApiResult.Error -> Pair(emptyList(), message)
        }

    private fun <T> ApiResult<JSONObject>.mapOne(parse: (JSONObject) -> T): Pair<T?, String?> =
        when (this) {
            is ApiResult.Success -> Pair(parse(data), null)
            is ApiResult.Error -> Pair(null, message)
        }
}
