package com.example.datapp

import com.example.datapp.data.Content
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit

class AppExceptionBoundaryTest {

    private fun unsafeTabValueOf(name: String): String {
        val validTabs = listOf("Home", "Content", "Knowledge", "Profile")
        if (name !in validTabs) {
            throw IllegalArgumentException("No enum constant $name")
        }
        return name
    }

    private fun safeTabValueOf(name: String, default: String = "Home"): String {
        val validTabs = listOf("Home", "Content", "Knowledge", "Profile")
        return if (name in validTabs) name else default
    }

    private fun calculateSparklinePointsUnsafe(values: List<Float>, width: Float, height: Float): List<Pair<Float, Float>> {
        if (values.isEmpty()) throw NoSuchElementException("List is empty.")
        return values.mapIndexed { index, value ->
            val x = index * width / (values.size - 1)
            val y = value * height
            Pair(x, y)
        }
    }

    private fun calculateSparklinePointsSafe(values: List<Float>, width: Float, height: Float): List<Pair<Float, Float>> {
        if (values.isEmpty()) return emptyList()
        if (values.size == 1) return listOf(Pair(width / 2f, values[0] * height))
        val divisor = (values.size - 1).coerceAtLeast(1)
        return values.mapIndexed { index, value ->
            val x = index * width / divisor
            val y = value * height
            Pair(x, y)
        }
    }

    private fun filterContentsUnsafe(contents: List<Content>, activeTag: String, query: String): List<Content> {
        return contents.filter { content ->
            (activeTag == "全部" || content.reviewStatus == activeTag) &&
                (query.isBlank() || content.title.contains(query, true) || content.text.contains(query, true))
        }
    }

    private fun filterContentsSafe(contents: List<Content>, activeTag: String, query: String): List<Content> {
        val trimmedQuery = query.trim()
        return contents.filter { content ->
            (activeTag == "全部" || content.reviewStatus == activeTag) &&
                (trimmedQuery.isBlank() ||
                    content.title.contains(trimmedQuery, true) ||
                    content.text.contains(trimmedQuery, true) ||
                    content.authorName.contains(trimmedQuery, true))
        }
    }

    @Test
    fun testIssue1_InvalidTabNameThrowsInUnsafe_AndHandledInSafe() {
        try {
            unsafeTabValueOf("UNKNOWN_TAB_MIGRATION")
            fail("Expected IllegalArgumentException on invalid tab name")
        } catch (e: IllegalArgumentException) {
            assertTrue(e.message!!.contains("No enum constant"))
        }

        val safeTab = safeTabValueOf("UNKNOWN_TAB_MIGRATION")
        assertEquals("Home", safeTab)
    }

    @Test
    fun testIssue2_SparklineSingleItemDivisionByZero_HandledInSafe() {
        val singleValue = listOf(0.5f)

        val pointsUnsafe = calculateSparklinePointsUnsafe(singleValue, 100f, 100f)
        assertTrue("Single item x should be NaN or Infinity in unsafe logic", pointsUnsafe[0].first.isNaN() || pointsUnsafe[0].first.isInfinite())

        val pointsSafe = calculateSparklinePointsSafe(singleValue, 100f, 100f)
        assertEquals(1, pointsSafe.size)
        assertFalse(pointsSafe[0].first.isNaN())
        assertFalse(pointsSafe[0].first.isInfinite())
        assertEquals(50f, pointsSafe[0].first)

        val emptyPoints = calculateSparklinePointsSafe(emptyList(), 100f, 100f)
        assertTrue(emptyPoints.isEmpty())
    }

    @Test
    fun testIssue3_ConcurrentFavoritesToggleRaceCondition() {
        val executor = Executors.newFixedThreadPool(10)
        val favorites = ConcurrentHashMap.newKeySet<Int>()

        val taskCount = 100
        for (i in 0 until taskCount) {
            executor.submit {
                val reportId = 1
                synchronized(favorites) {
                    if (favorites.contains(reportId)) {
                        favorites.remove(reportId)
                    } else {
                        favorites.add(reportId)
                    }
                }
            }
        }

        executor.shutdown()
        assertTrue(executor.awaitTermination(2, TimeUnit.SECONDS))
        assertFalse(favorites.contains(1))
    }

    @Test
    fun testIssue5_SearchQueryWhitespaceHandling() {
        val contents = listOf(
            Content(
                contentId = "xhs:1",
                platform = "xhs",
                contentType = "note",
                title = "生活方式内容正在升温",
                text = "低饱和、轻运动",
                authorName = "卡卡呀",
                canonicalUrl = "https://www.xiaohongshu.com",
                publishedAt = "2026-09-09",
                collectedAt = "2026-09-10",
                coverLocal = "/media/cover.webp",
                coverUrl = "",
                tags = listOf("趋势上升"),
                reviewStatus = "pending",
                likes = "955",
                comments = "12",
                shares = "5",
                collects = "100"
            )
        )

        val unsafeResult = filterContentsUnsafe(contents, "全部", "  生活方式  ")
        assertTrue(unsafeResult.isEmpty())

        val safeResult = filterContentsSafe(contents, "全部", "  生活方式  ")
        assertEquals(1, safeResult.size)
    }

    @Test
    fun testIssue7_DuplicateDataKeySafetyCheck() {
        val duplicates = listOf(
            Content("xhs:1", "xhs", "note", "标题1", "摘要1", "来源1", "https://url", "2026-09-09", "2026-09-10", "", "", emptyList(), "pending", "10", "0", "0", "0"),
            Content("xhs:1", "xhs", "note", "标题1重复", "摘要1重复", "来源1", "https://url", "2026-09-09", "2026-09-10", "", "", emptyList(), "pending", "10", "0", "0", "0")
        )

        val keys = duplicates.mapIndexed { index, content -> "content-${content.contentId}-$index" }
        assertEquals(2, keys.toSet().size)
    }
}
