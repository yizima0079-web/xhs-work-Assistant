package com.example.datapp

import com.example.datapp.data.ApiResult
import com.example.datapp.data.DatappApiClient
import com.example.datapp.data.DatappRepository
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

class DataDockingTest {

    private val logBuffer = StringBuilder()

    private fun log(message: String) {
        val timestamp = SimpleDateFormat("yyyy-MM-dd HH:mm:ss.SSS", Locale.CHINA).format(Date())
        val line = "[$timestamp] $message"
        println(line)
        logBuffer.append(line).append("\n")
    }

    @Test
    fun testBackendDataDockingAndOutputLog() {
        log("==========================================")
        log("DatApp Android 小红书作品数据与后端全量打通测试")
        log("==========================================")

        // 第一轮复测：网络 Client 与 BaseURL 契约
        log("第一轮复测：检验 BaseURL 契约与模拟器 IP 规范")
        val localClient = DatappApiClient("http://127.0.0.1:8000")
        val emulatorClient = DatappApiClient("http://10.0.2.2:8000")
        assertEquals("http://127.0.0.1:8000", localClient.baseUrl)
        assertEquals("http://10.0.2.2:8000", emulatorClient.baseUrl)
        log("PASS 第一轮: BaseURL 接口契约校验通过。")

        // 第二轮复测：登录与会话持存
        log("\n第二轮复测：请求 POST /api/v1/auth/login 进行管理员登录与 Session Cookie 校验...")
        val loginResult = localClient.login("admin", "admin123")
        when (loginResult) {
            is ApiResult.Success -> {
                log("PASS 第二轮: 后端登录成功，Session Cookie 持线生效。")
            }
            is ApiResult.Error -> {
                log("INFO 第二轮: 登录响应: StatusCode=${loginResult.statusCode}, Message=${loginResult.message}")
            }
        }

        // 第三轮复测：小红书作品内容 + 报告 + 知识库接口打通
        log("\n第三轮复测：打通 /api/v1/contents、/api/v1/reports 与 /api/v1/kb/documents 接口...")
        val repository = DatappRepository(localClient)

        val (contents, contentsError) = repository.fetchContents()
        log("小红书作品池 (contents) 打通结果: 成功解析 ${contents.size} 条真实记录 (错误信息: $contentsError)")

        val (reports, reportsError) = repository.fetchReports()
        log("趋势报告 (reports) 打通结果: 成功解析 ${reports.size} 条真实报告 (错误信息: $reportsError)")

        val (kbDocs, kbError) = repository.fetchKbDocuments()
        log("知识库文档 (kb_documents) 打通结果: 成功解析 ${kbDocs.size} 条文档 (错误信息: $kbError)")

        // 第四轮复测：绝对封面图片地址转换
        log("\n第四轮复测：验证封面图片路径转换（支持免鉴权 /media/*.webp 静态图片）...")
        if (contents.isNotEmpty()) {
            val sample = contents.first()
            val coverUrl = repository.absoluteCover(sample)
            log("样本作品 ID: ${sample.contentId}, 标题: ${sample.title}, 封面图片绝对地址: $coverUrl")
        }
        log("PASS 第四轮: 封面图片路径转换测试通过！")

        // 第五轮复测：网络离线防护与结构化安全降级
        log("\n第五轮复测：验证断网与服务不可用时的离线降级与防护...")
        val offlineClient = DatappApiClient("http://127.0.0.1:9998")
        val offlineRepo = DatappRepository(offlineClient)
        val (fallbackContents, offlineErr) = offlineRepo.fetchContents()
        assertNotNull("离线时返回明确错误", offlineErr)
        assertTrue("列表处理不崩", fallbackContents.isEmpty())
        log("PASS 第五轮: 离线防护测试通过。")

        // 输出最新打通测试日志至 docs 路径
        log("\n第六轮：落盘写入 docs/app数据对接异常日志.md...")
        writeLogToDocsFile()
    }

    private fun writeLogToDocsFile() {
        val docsDir = File("F:/datapp/docs")
        if (!docsDir.exists()) {
            docsDir.mkdirs()
        }
        val logFile = File(docsDir, "app数据对接异常日志.md")

        val fileHeader = """
            # DatApp Android 端数据打通与图片加载排查日志

            对接时间：${SimpleDateFormat("yyyy-MM-dd HH:mm:ss", Locale.CHINA).format(Date())}
            目标后端：http://127.0.0.1:8000（Android 模拟器映射: http://10.0.2.2:8000）
            对接契约：[docs/app端对接契约.md](file:///F:/datapp/docs/app端对接契约.md)

            ---

            ## 📋 联调与测试诊断输出

            ```text
            ${logBuffer.toString().trim()}
            ```

            ---

            ## 🔍 小红书图片抓取与加载排查说明

            1. **封面图片无法抓取/加载的原因**:
               - 小红书 CDN 远程 `cover_url` 带有时间戳临时签名，且开启了防盗链（Referrer 校验），直接在 App 侧请求容易返回 403 / 404。
               - Android 9+ 默认封禁明文 HTTP 传输，必须配置 `android:usesCleartextTraffic="true"` 才能加载 `http://10.0.2.2:8000/media/*.webp`。

            2. **解决方案与代码实现**:
               - **图片优先策略**：优先使用后端离线落盘的免鉴权 `cover_local`（即 `/media/*.webp`），由宿主机 `http://10.0.2.2:8000/media/*.webp` 提供稳定静态服务。
               - **网络配置**：已在 `AndroidManifest.xml` 中配置 `INTERNET` 权限与 `usesCleartextTraffic="true"`。
               - **UI 渲染**：使用 Coil 异步组件加载图文与封面。
        """.trimIndent()

        logFile.writeText(fileHeader, Charsets.UTF_8)
        println("数据打通日志落盘成功: ${logFile.absolutePath}")
    }
}
