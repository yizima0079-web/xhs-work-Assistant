package com.example.datapp.data

import android.content.Context
import org.json.JSONArray
import org.json.JSONObject

/**
 * 一轮问答的历史快照。
 *
 * 字段与 Web 看板 `localStorage['datapp.kb.qa-history.v1']` 同构
 * （id / query / answer / answered / createdAt），两端读到的历史是同一套语义。
 */
data class QaTurnRecord(
    val id: String,
    val query: String,
    val answer: String,
    val answered: Boolean,
    val createdAt: String,
) {
    fun toJson(): JSONObject = JSONObject().apply {
        put("id", id)
        put("query", query)
        put("answer", answer)
        put("answered", answered)
        put("createdAt", createdAt)
    }

    companion object {
        fun from(o: JSONObject) = QaTurnRecord(
            id = o.optString("id"),
            query = o.optString("query"),
            answer = o.optString("answer"),
            answered = o.optBoolean("answered"),
            createdAt = o.optString("createdAt"),
        )
    }
}

/**
 * 知识库问答的历史记录 + 会话记忆开关。
 *
 * 开关与历史分开存：开关是**用户意图**，历史清空后仍然要保留，否则用户每次清完历史
 * 都得重新关一次记忆。
 *
 * 只落盘服务端已经过红线过滤的答案文本。**拒答轮次（`answered=false`）同样入库** ——
 * 「问过、但知识库里没有依据」本身就是该留下的记录，删掉它等于粉饰知识库的覆盖缺口。
 *
 * 历史里**不存引用与相似度**：那些是结论的证据链，脱离当次检索快照后无法复现，
 * 存下来只会在回看时给出一个看起来有据、实际查无对应的假象。历史条目只用来回填问题。
 */
class QaHistoryStore(context: Context) {
    private val prefs = context.applicationContext
        .getSharedPreferences("datapp_qa_history", Context.MODE_PRIVATE)

    /** 记忆开关：开启时把最近 [MAX_CONTEXT_TURNS] 轮问答作为上下文发给服务端。默认开。 */
    var memoryEnabled: Boolean
        get() = prefs.getBoolean(KEY_MEMORY, true)
        set(value) {
            prefs.edit().putBoolean(KEY_MEMORY, value).apply()
        }

    fun load(): List<QaTurnRecord> {
        val raw = prefs.getString(KEY_HISTORY, null) ?: return emptyList()
        return try {
            val arr = JSONArray(raw)
            (0 until arr.length())
                .mapNotNull { i -> arr.optJSONObject(i)?.let(QaTurnRecord::from) }
                // 问题为空的条目是脏数据，留着只会在列表里点出一片空白
                .filter { it.query.isNotBlank() }
                .takeLast(MAX_TURNS)
        } catch (e: Exception) {
            // 存储损坏不该让知识库打不开：当没有历史，下一次写入自然覆盖掉坏数据
            emptyList()
        }
    }

    fun save(turns: List<QaTurnRecord>) {
        val arr = JSONArray()
        turns.takeLast(MAX_TURNS).forEach { arr.put(it.toJson()) }
        prefs.edit().putString(KEY_HISTORY, arr.toString()).apply()
    }

    fun clear() {
        prefs.edit().remove(KEY_HISTORY).apply()
    }

    companion object {
        const val MAX_TURNS = 20

        /** 服务端 `KbAskRequest.history` 卡在 `max_length=6`，超一条整发请求被拒。 */
        const val MAX_CONTEXT_TURNS = 6

        private const val KEY_HISTORY = "qa_turns"
        private const val KEY_MEMORY = "memory_enabled"
    }
}
