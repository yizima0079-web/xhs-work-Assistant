package com.example.datapp

import com.example.datapp.data.normalizeBaseUrl
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/**
 * 后端地址规范化的纯逻辑用例。
 *
 * 为什么值得单独测：这个函数的产出会被**存进设备**并直接参与拼 URL，
 * 它错一次，用户后来的每一次请求都是错的、且重启也不会自愈 —— 而错误表现
 * 又是 okhttp 那句误导性极强的 `unexpected end of stream`（见 gradle.properties
 * 里的踩坑记录），排查成本远高于在这里写十几个断言。
 */
class BaseUrlNormalizeTest {

    @Test
    fun testAddsMissingScheme() {
        assertEquals("http://192.168.0.109:8000", normalizeBaseUrl("192.168.0.109:8000"))
        assertEquals("http://127.0.0.1:8000", normalizeBaseUrl("127.0.0.1:8000"))
    }

    @Test
    fun testKeepsExistingScheme() {
        assertEquals("http://192.168.0.109:8000", normalizeBaseUrl("http://192.168.0.109:8000"))
        // https 绝不能被改写成 http —— 那会把校验和证书一起降级掉。
        assertEquals("https://datapp.example.com", normalizeBaseUrl("https://datapp.example.com"))
    }

    @Test
    fun testSchemeIsCaseInsensitive() {
        // 手机输入法首字母自动大写，`HTTP://x` 不能被补成 `http://HTTP://x`。
        assertEquals("HTTP://192.168.0.109:8000", normalizeBaseUrl("HTTP://192.168.0.109:8000"))
        assertEquals("HTTPS://datapp.example.com", normalizeBaseUrl("HTTPS://datapp.example.com"))
    }

    @Test
    fun testStripsAllTrailingSlashes() {
        // 一个都不能留：endpoint 自带前导 `/`，留一个就拼出双斜杠路径。
        assertEquals("http://192.168.0.109:8000", normalizeBaseUrl("http://192.168.0.109:8000/"))
        assertEquals("http://192.168.0.109:8000", normalizeBaseUrl("http://192.168.0.109:8000///"))
        assertEquals("http://192.168.0.109:8000", normalizeBaseUrl("192.168.0.109:8000/"))
    }

    @Test
    fun testTrimsSurroundingWhitespace() {
        // 从聊天窗口复制地址，尾随空格几乎是必然的。
        assertEquals("http://192.168.0.109:8000", normalizeBaseUrl("  http://192.168.0.109:8000  "))
        assertEquals("http://192.168.0.109:8000", normalizeBaseUrl("\thttp://192.168.0.109:8000/\n"))
    }

    @Test
    fun testBlankInputReturnsNull() {
        assertNull(normalizeBaseUrl(""))
        assertNull(normalizeBaseUrl("   "))
        assertNull(normalizeBaseUrl("\n\t "))
        // 只剩斜杠：trimEnd 后为空。
        assertNull(normalizeBaseUrl("///"))
        assertNull(normalizeBaseUrl("  //  "))
    }

    @Test
    fun testHostlessUrlReturnsNull() {
        // `http://` 在 Java 的 URL 里不一定抛异常，所以实现里显式查了 host。
        assertNull(normalizeBaseUrl("http://"))
        assertNull(normalizeBaseUrl("https://"))
        assertNull(normalizeBaseUrl("http:///"))
    }

    @Test
    fun testKeepsPortAndPath() {
        assertEquals("http://192.168.0.109:8000", normalizeBaseUrl("192.168.0.109:8000"))
        // 带路径不是本函数的职责范围 —— 它只管合法性和尾斜杠，不擅自裁剪用户填的路径。
        assertEquals(
            "http://192.168.0.109:8000/datapp",
            normalizeBaseUrl("http://192.168.0.109:8000/datapp/"),
        )
    }

    @Test
    fun testNormalizationIsIdempotent() {
        // 存进去的值会被再规范化一次（保存时一次、自动探测命中时又一次），
        // 不幂等的话第二次就会把地址改坏。
        val once = normalizeBaseUrl("  192.168.0.109:8000///  ")
        assertEquals("http://192.168.0.109:8000", once)
        assertEquals(once, normalizeBaseUrl(once!!))
    }
}
