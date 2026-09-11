package com.example.datapp.data

import org.json.JSONArray
import org.json.JSONObject

/**
 * 贴近后端契约的领域模型（契约依据 docs/app端对接契约.md §2 / §3）。
 *
 * 全部手写 fromJson —— app 不引序列化库，沿用仓库层既有的 org.json 风格。
 * 解析一律用 opt 系列给兜底：字段缺失时得到一个空值对象，而不是抛异常让整页崩掉。
 *
 * 与 TempData.kt 里的 Insight / Report 区分：那两个是早期 UI 的简化模型，
 * 这里是与服务端字段一一对应的契约模型。
 */

// ---------------------------------------------------------------- 内容池

data class Content(
    val contentId: String,
    val platform: String,
    val contentType: String,
    val title: String,
    val text: String,
    val authorName: String,
    val canonicalUrl: String,
    val publishedAt: String,
    val collectedAt: String,
    /** 本地封面（/media/xx.webp），**优先用它**，见契约 §1.4 */
    val coverLocal: String,
    /** 远程封面，xhscdn 带时效签名，过期即 403，仅作兜底 */
    val coverUrl: String,
    val tags: List<String>,
    val reviewStatus: String,
    val likes: String,
    val comments: String,
    val shares: String,
    val collects: String,
) {
    /** 审核状态中文（对齐前端 lib/labels.ts 的 REVIEW_META） */
    val reviewStatusLabel: String
        get() = when (reviewStatus) {
            "pending" -> "未审核"
            "extracted" -> "待人工"
            "approved" -> "可信"
            "rejected" -> "不采信"
            "disputed" -> "存争议"
            else -> reviewStatus
        }

    /** 可信 / 待人工 —— 用于列表上色 */
    val isTrusted: Boolean get() = reviewStatus == "approved"

    companion object {
        fun from(o: JSONObject): Content {
            val eng = o.optJSONObject("engagement") ?: JSONObject()
            return Content(
                contentId = o.optString("content_id"),
                platform = o.optString("platform", "xhs"),
                contentType = o.optString("content_type"),
                title = o.optString("title").ifBlank { "(无标题)" },
                text = o.optString("text"),
                authorName = o.optString("author_name"),
                canonicalUrl = o.optString("canonical_url"),
                publishedAt = o.optString("published_at").take(10),
                collectedAt = o.optString("collected_at").take(19).replace("T", " "),
                coverLocal = o.optString("cover_local"),
                coverUrl = o.optString("cover_url"),
                tags = o.optJSONArray("tags").toStringList(),
                reviewStatus = o.optString("review_status", "pending"),
                likes = eng.opt("likes").asText(),
                comments = eng.opt("comments").asText(),
                shares = eng.opt("shares").asText(),
                collects = eng.opt("collects").asText(),
            )
        }
    }
}

// ---------------------------------------------------------------- 审核

data class Evidence(
    val evidenceId: String,
    val sourceKind: String,
    val excerpt: String,
    val supports: Boolean?,
) {
    companion object {
        fun from(o: JSONObject): Evidence = Evidence(
            evidenceId = o.optString("evidence_id"),
            sourceKind = o.optString("source_kind"),
            excerpt = o.optString("excerpt"),
            supports = if (o.isNull("supports")) null else o.optBoolean("supports"),
        )
    }
}

data class Claim(
    val claimId: String,
    val contentId: String,
    val text: String,
    val status: String,
    val confidence: Double?,
    val claimType: String,
    val batchSeq: Int,
) {
    /** 断言状态中文（对齐前端 lib/labels.ts 的 CLAIM_STATUS） */
    val statusLabel: String
        get() = when (status) {
            "unverified" -> "未验证"
            "supported" -> "支持"
            "contradicted" -> "反驳"
            "unclear" -> "存疑"
            else -> status
        }

    val confidencePercent: Int
        get() = ((confidence ?: 0.0) * 100).toInt()

    /** 置信度低于阈值（服务端 REVIEW_MIN_CONFIDENCE = 0.6）时，单靠状态推不动内容审核结论 */
    val isLowConfidence: Boolean get() = (confidence ?: 0.0) < 0.6

    companion object {
        fun from(o: JSONObject): Claim = Claim(
            claimId = o.optString("claim_id"),
            contentId = o.optString("content_id"),
            text = o.optString("text"),
            status = o.optString("status", "unverified"),
            confidence = if (o.isNull("confidence")) null else o.optDouble("confidence"),
            claimType = o.optString("claim_type"),
            batchSeq = o.optInt("batch_seq", 1),
        )
    }
}

data class ClaimSnapshot(val claim: Claim, val evidence: List<Evidence>) {
    companion object {
        fun from(o: JSONObject): ClaimSnapshot = ClaimSnapshot(
            claim = Claim.from(o.optJSONObject("claim") ?: JSONObject()),
            evidence = o.optJSONArray("evidence").toObjectList { Evidence.from(it) },
        )
    }
}

data class ReviewBatch(
    val batchId: String,
    val batchSeq: Int,
    val claimCount: Int,
    val createdAt: String,
    val active: Boolean,
) {
    companion object {
        fun from(o: JSONObject): ReviewBatch = ReviewBatch(
            batchId = o.optString("batch_id"),
            batchSeq = o.optInt("batch_seq", 1),
            claimCount = o.optInt("claim_count"),
            createdAt = o.optString("created_at").take(19).replace("T", " "),
            active = o.optBoolean("active"),
        )
    }
}

data class ContentReviewDetail(
    val claims: List<ClaimSnapshot>,
    val batches: List<ReviewBatch>,
    val batchId: String?,
) {
    companion object {
        fun from(o: JSONObject): ContentReviewDetail = ContentReviewDetail(
            claims = o.optJSONArray("claims").toObjectList { ClaimSnapshot.from(it) },
            batches = o.optJSONArray("batches").toObjectList { ReviewBatch.from(it) },
            batchId = o.optString("batch_id").ifBlank { null },
        )
    }
}

// ---------------------------------------------------------------- 分析

/** 一条归因因子。服务端 `viral_reasons` / `flat_reasons` 是同一个结构 `{factor, evidence, confidence}`。 */
data class AnalysisReason(
    val factor: String,
    val evidence: String,
    val confidence: Double,
) {
    /** 一行式展示：因子 —— 依据 */
    val line: String get() = if (evidence.isBlank()) factor else "$factor —— $evidence"

    companion object {
        fun from(o: JSONObject): AnalysisReason = AnalysisReason(
            factor = o.optString("factor"),
            evidence = o.optString("evidence"),
            confidence = o.optDouble("confidence", 0.0),
        )
    }
}

data class Analysis(
    val analysisId: String,
    val contentId: String,
    val verdict: String,
    val summary: String,
    val topic: String,
    val confidence: Double,
    val baselineCount: Int,
    val comparedWith: List<String>,
    val focus: String,
    val markdown: String,
    val createdAt: String,
    val inKb: Boolean,
    val viralReasons: List<AnalysisReason>,
    val flatReasons: List<AnalysisReason>,
    val hooks: List<String>,
    val audience: List<String>,
    val suggestions: List<String>,
    val limitations: List<String>,
    /** 分析 schema 版本 —— 只进「数据存证」，正文里不需要 */
    val analysisVersion: String,
) {
    val verdictLabel: String
        get() = when (verdict) {
            "viral" -> "爆款"
            "flat" -> "表现平淡"
            "uncertain" -> "不确定"
            else -> verdict
        }

    val confidencePercent: Int get() = (confidence * 100).toInt()

    /**
     * 归因因子按结论二选一：爆款看 `viral_reasons`，平淡看 `flat_reasons`。
     *
     * 服务端两个数组只会其一非空，但这里仍然互相兜底 —— 早期版本只解析了
     * `viral_reasons`，导致「表现平淡」的结论在 app 上归因栏是**空的**。
     * 兜底是为了下次再有类似的字段错配时，界面退化成「显示另一组」而不是「一片空白」。
     */
    val attributionReasons: List<AnalysisReason>
        get() = when (verdict) {
            "flat" -> flatReasons.ifEmpty { viralReasons }
            else -> viralReasons.ifEmpty { flatReasons }
        }

    val attributionTitle: String
        get() = when (verdict) {
            "viral" -> "爆点归因"
            "flat" -> "平淡归因"
            else -> "归因因子"
        }

    companion object {
        fun from(o: JSONObject): Analysis {
            val p = o.optJSONObject("payload") ?: JSONObject()
            val cmp = p.optJSONObject("comparison") ?: JSONObject()
            return Analysis(
                analysisId = o.optString("analysis_id"),
                contentId = o.optString("content_id"),
                verdict = o.optString("verdict", "uncertain"),
                summary = p.optString("summary"),
                topic = p.optString("topic"),
                confidence = p.optDouble("confidence", 0.0),
                baselineCount = cmp.optInt("baseline_count"),
                comparedWith = o.optJSONArray("compared_with").toStringList(),
                focus = o.optString("focus"),
                markdown = o.optString("markdown"),
                createdAt = o.optString("created_at").take(19).replace("T", " "),
                inKb = o.optBoolean("in_kb"),
                viralReasons = p.optJSONArray("viral_reasons").toObjectList { AnalysisReason.from(it) },
                flatReasons = p.optJSONArray("flat_reasons").toObjectList { AnalysisReason.from(it) },
                hooks = p.optJSONArray("hooks").toStringList(),
                audience = p.optJSONArray("audience").toStringList(),
                suggestions = p.optJSONArray("suggestions").toStringList(),
                limitations = p.optJSONArray("limitations").toStringList(),
                analysisVersion = p.optString("schema_version")
                    .ifBlank { o.optString("schema_version") },
            )
        }
    }
}

// ---------------------------------------------------------------- 报告

/** 一条爆点模式。`counterexamples` 只留条数 —— 裸 content_id 对 app 用户没有意义。 */
data class ReportPattern(
    val pattern: String,
    val confidence: Double,
    val counterexampleCount: Int,
) {
    companion object {
        fun from(o: JSONObject): ReportPattern = ReportPattern(
            pattern = o.optString("pattern"),
            confidence = o.optDouble("confidence", 0.0),
            counterexampleCount = o.optJSONArray("counterexamples")?.length() ?: 0,
        )
    }
}

/**
 * 报告详情。
 *
 * **全部结论从 `payload` 读，不解析 `markdown`。** 服务端产出 JSON + Markdown 双格式，
 * 两者是同一份内容的两种排版；markdown 那份还带着「审计原文」JSON 块与报告 ID 之类的
 * 审计信息 —— 那是给服务端和 web 用的，app 是结果消费端，只要结构化的结论。
 *
 * `markdown` 字段仍然保留（服务端契约的一部分，也是以后「查看原文」的入口），只是不渲染。
 */
data class ReportDetail(
    val reportId: String,
    val title: String,
    val contentIds: List<String>,
    val summary: String,
    val viralPatterns: List<ReportPattern>,
    /** 表现风格：四组「标签 to 要点」，**空组已在解析时丢掉** */
    val presentationStyle: List<Pair<String, List<String>>>,
    val personaHypotheses: List<String>,
    val personaConfidence: Double,
    val trendTopics: List<String>,
    val trendDirection: String,
    val trendWindow: String,
    val limitations: List<String>,
    val analysisVersion: String,
    val markdown: String,
    val createdAt: String,
) {
    /** 趋势方向中文 + 符号（服务端是 rising/stable/falling/uncertain 机码） */
    val trendDirectionLabel: String
        get() = when (trendDirection) {
            "rising" -> "↑ 上升"
            "stable" -> "→ 平稳"
            "falling" -> "↓ 下降"
            else -> "? 不确定"
        }

    /** payload 里什么都没有 —— 页面据此显示空态，而不是铺一整片空白卡片 */
    val hasStructuredContent: Boolean
        get() = summary.isNotBlank() || viralPatterns.isNotEmpty() ||
            presentationStyle.isNotEmpty() || personaHypotheses.isNotEmpty() ||
            trendTopics.isNotEmpty() || limitations.isNotEmpty()

    companion object {
        /** presentation_style 的四个键 → 中文；**map 的顺序就是页面展示顺序** */
        private val STYLE_LABELS = linkedMapOf(
            "text" to "文案",
            "visual" to "画面",
            "video" to "视频",
            "interaction" to "互动",
        )

        fun from(o: JSONObject): ReportDetail {
            val p = o.optJSONObject("payload") ?: JSONObject()
            val style = p.optJSONObject("presentation_style") ?: JSONObject()
            val persona = p.optJSONObject("account_persona") ?: JSONObject()
            val trend = p.optJSONObject("trend_direction") ?: JSONObject()
            return ReportDetail(
                reportId = o.optString("report_id"),
                title = o.optString("title", "审核报告"),
                contentIds = o.optJSONArray("content_ids").toStringList(),
                summary = p.optString("executive_summary"),
                viralPatterns = p.optJSONArray("viral_patterns")
                    .toObjectList { ReportPattern.from(it) }
                    .filter { it.pattern.isNotBlank() },
                // 空组直接丢掉：visual / video 常为空，摆一行空标题只是噪音
                presentationStyle = STYLE_LABELS.mapNotNull { (key, label) ->
                    style.optJSONArray(key).toStringList()
                        .takeIf { it.isNotEmpty() }
                        ?.let { label to it }
                },
                personaHypotheses = persona.optJSONArray("hypotheses").toStringList(),
                personaConfidence = persona.optDouble("confidence", 0.0),
                trendTopics = trend.optJSONArray("topics").toStringList(),
                // org.json 的 optString 在值是 JSON null 时返回**字符串 "null"**，
                // ifBlank 兜不住（Models.kt 里已踩过一次），必须先 isNull 判一下。
                trendDirection = if (trend.isNull("direction")) "" else trend.optString("direction"),
                trendWindow = if (trend.isNull("window")) "" else trend.optString("window"),
                limitations = p.optJSONArray("limitations").toStringList(),
                analysisVersion = o.optString("schema_version")
                    .ifBlank { p.optString("analysis_version") },
                markdown = o.optString("markdown"),
                createdAt = o.optString("created_at").take(19).replace("T", " "),
            )
        }
    }
}

// ---------------------------------------------------------------- 知识库

data class KbDocument(
    val docId: String,
    val sourceType: String,
    val docType: String,
    val title: String,
    val author: String,
    val tags: List<String>,
    val url: String,
    val status: String,
    val chunkCount: Int,
    val qaPairCount: Int,
    val qaApprovedCount: Int,
    val createdAt: String,
) {
    val statusLabel: String
        get() = when (status) {
            "pending" -> "待向量化"
            "embedding" -> "向量化中"
            "ready" -> "可检索"
            "failed" -> "失败"
            else -> status
        }

    val sourceLabel: String
        get() = when (sourceType) {
            "analysis" -> "分析"
            "content" -> "内容"
            "manual" -> "手动"
            else -> sourceType
        }

    /** 只有 pending / failed 能点「向量化」——其余状态重复提交无意义 */
    val canVectorize: Boolean get() = status == "pending" || status == "failed"

    val isQaDoc: Boolean get() = docType == "qa"

    companion object {
        fun from(o: JSONObject): KbDocument = KbDocument(
            docId = o.optString("doc_id"),
            sourceType = o.optString("source_type"),
            docType = o.optString("doc_type", "general"),
            title = o.optString("title").ifBlank { "(无标题)" },
            // 同 Models.kt 里已踩过的坑：optString 在值是 JSON null 时返回**字符串 "null"**，
            // ifBlank 兜不住 —— author 为 null 的文档会在列表里显示成「null · 2026-09-10」。
            author = if (o.isNull("author")) "" else o.optString("author"),
            tags = o.optJSONArray("tags").toStringList(),
            url = if (o.isNull("url")) "" else o.optString("url"),
            status = o.optString("status", "pending"),
            chunkCount = o.optInt("chunk_count"),
            qaPairCount = o.optInt("qa_pair_count"),
            qaApprovedCount = o.optInt("qa_approved_count"),
            createdAt = o.optString("created_at").take(19).replace("T", " "),
        )
    }
}

data class KbSearchHit(
    val chunkId: String,
    val docId: String,
    val text: String,
    val score: Double,
    val title: String,
    val sourceType: String,
) {
    val scorePercent: Int get() = (score * 100).toInt()

    companion object {
        fun from(o: JSONObject): KbSearchHit {
            val meta = o.optJSONObject("meta") ?: JSONObject()
            return KbSearchHit(
                chunkId = o.optString("chunk_id"),
                docId = o.optString("doc_id"),
                text = o.optString("text"),
                score = o.optDouble("score", 0.0),
                // 没标题写「未命名来源」，不回落成 doc_id —— 那是机器码，不是给人看的标题
                title = meta.optString("title").ifBlank { "未命名来源" },
                sourceType = meta.optString("source_type"),
            )
        }
    }
}

data class KbCitation(
    val chunkId: String,
    val docId: String,
    val title: String,
    val text: String,
    val score: Double,
    val verified: Boolean,
    val question: String?,
) {
    val scorePercent: Int get() = (score * 100).toInt()

    companion object {
        fun from(o: JSONObject): KbCitation = KbCitation(
            chunkId = o.optString("chunk_id"),
            docId = o.optString("doc_id"),
            title = o.optString("title"),
            text = o.optString("text"),
            score = o.optDouble("score", 0.0),
            verified = o.optBoolean("verified"),
            question = o.optString("question").ifBlank { null },
        )
    }
}

data class KbAnswer(
    val answered: Boolean,
    val answer: String,
    val citations: List<KbCitation>,
    val topScore: Double,
    val retrievalCount: Int,
    val limitations: List<String>,
    val reason: String,
) {
    /** 拒答原因中文（服务端的 reason 是机器码） */
    val reasonLabel: String
        get() = when (reason) {
            "empty_retrieval" -> "未检索到任何内容"
            "below_threshold" -> "最高相似度低于拒答阈值"
            "no_valid_citation" -> "检索结果中无有效引用"
            else -> reason
        }

    companion object {
        fun from(o: JSONObject): KbAnswer = KbAnswer(
            answered = o.optBoolean("answered"),
            answer = o.optString("answer"),
            citations = o.optJSONArray("citations").toObjectList { KbCitation.from(it) },
            topScore = o.optDouble("top_score", 0.0),
            retrievalCount = o.optInt("retrieval_count"),
            limitations = o.optJSONArray("limitations").toStringList(),
            reason = o.optString("reason"),
        )
    }
}

data class KbQaPair(
    val qaId: String,
    val question: String,
    val answer: String,
    val status: String,
    val dimensions: List<Pair<String, String>>,
    val tags: List<String>,
) {
    val statusLabel: String
        get() = when (status) {
            "draft" -> "草稿"
            "approved" -> "已通过"
            "rejected" -> "已驳回"
            else -> status
        }

    val isDraft: Boolean get() = status == "draft"

    companion object {
        /** 六维拆解的键名 → 中文（对齐前端 KnowledgeBase 的 dimensions 展示） */
        private val DIM_LABELS = linkedMapOf(
            "technique" to "表现手法",
            "persona" to "IP人设",
            "hook" to "开头钩子",
            "structure" to "结构节奏",
            "transfer" to "迁移建议",
            "transfer_risk" to "失败风险",
        )

        fun from(o: JSONObject): KbQaPair {
            val dims = o.optJSONObject("dimensions") ?: JSONObject()
            val pairs = mutableListOf<Pair<String, String>>()
            for ((key, label) in DIM_LABELS) {
                if (!dims.has(key) || dims.isNull(key)) continue
                val value = when (val raw = dims.opt(key)) {
                    is JSONArray -> (0 until raw.length()).joinToString("；") { raw.optString(it) }
                    null -> ""
                    else -> raw.toString()
                }
                if (value.isNotBlank()) pairs.add(label to value)
            }
            return KbQaPair(
                qaId = o.optString("qa_id"),
                question = o.optString("question"),
                answer = o.optString("answer"),
                status = o.optString("status", "draft"),
                dimensions = pairs,
                tags = o.optJSONArray("tags").toStringList(),
            )
        }
    }
}

// ---------------------------------------------------------------- 采集申请

/**
 * app 提交的采集申请（服务端 collection_requests 表）。
 *
 * **app 只能提交，不能放行** —— 服务端对设备令牌关死了决策端点（PATCH 一律 401），
 * 放行权在 web 看板的管理员手里。这是「令牌编在 APK 里可被反编译」那条边界。
 */
data class CollectionRequest(
    val id: String,
    val target: String,
    val maxItems: Int,
    val status: String,
    val runId: String?,
    val note: String?,
    val createdAt: String,
) {
    val statusLabel: String
        get() = when (status) {
            "pending" -> "待放行"
            "approved" -> "已放行"
            "rejected" -> "已驳回"
            else -> status
        }

    companion object {
        fun from(o: JSONObject): CollectionRequest = CollectionRequest(
            id = o.optString("id"),
            target = o.optString("target"),
            maxItems = o.optInt("max_items", 10),
            status = o.optString("status").ifBlank { "pending" },
            // 注意：org.json 的 optString 在值是 JSON null 时返回**字符串 "null"**，
            // 直接 ifBlank 兜不住，必须先 isNull 判一次 —— 否则界面上会出现
            // 「驳回理由：null」这种把 null 当内容显示的假数据。
            runId = if (o.isNull("run_id")) null else o.optString("run_id").ifBlank { null },
            note = if (o.isNull("note")) null else o.optString("note").ifBlank { null },
            createdAt = o.optString("created_at").take(19).replace("T", " "),
        )
    }
}

// ---------------------------------------------------------------- JSON 小工具

/** 把任意 JSON 标量转成展示用字符串（null → 空串） */
private fun Any?.asText(): String = when (this) {
    null, JSONObject.NULL -> ""
    is Double -> if (this == this.toLong().toDouble()) this.toLong().toString() else this.toString()
    else -> this.toString()
}

private fun JSONArray?.toStringList(): List<String> {
    if (this == null) return emptyList()
    val out = mutableListOf<String>()
    for (i in 0 until length()) {
        val s = optString(i)
        if (s.isNotBlank()) out.add(s)
    }
    return out
}

private fun <T> JSONArray?.toObjectList(parse: (JSONObject) -> T): List<T> {
    if (this == null) return emptyList()
    val out = mutableListOf<T>()
    for (i in 0 until length()) {
        optJSONObject(i)?.let { out.add(parse(it)) }
    }
    return out
}
