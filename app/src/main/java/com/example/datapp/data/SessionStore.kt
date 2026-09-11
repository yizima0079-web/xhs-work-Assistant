package com.example.datapp.data

import android.content.Context

/**
 * 本机设置持久化：管理员会话 Cookie + 后端地址。
 *
 * Cookie 的目的只有一个：app 重启后不用重新输密码。服务端会话本身 12 小时过期
 * （`DATAPP_AUTH_SESSION_HOURS`），过期后任意请求返回 401，[DatappApp] 会把用户弹回登录页。
 * 存的是**服务端下发的会话 Cookie**，不是密码 —— 密码在登录成功后就不再持有。
 *
 * [baseUrl] 是设备配置而非会话状态，因此 [clear] **不会**清它（见该方法的说明）。
 *
 * 全部落在 app 私有目录（MODE_PRIVATE），其他应用读不到。
 */
class SessionStore(context: Context) {
    private val prefs = context.applicationContext
        .getSharedPreferences("datapp_session", Context.MODE_PRIVATE)

    var cookie: String?
        get() = prefs.getString(KEY_COOKIE, null)
        set(value) {
            prefs.edit().apply {
                if (value.isNullOrBlank()) remove(KEY_COOKIE) else putString(KEY_COOKIE, value)
            }.apply()
        }

    /** 上次登录的账号，仅用于回填输入框（不存密码） */
    var lastUsername: String?
        get() = prefs.getString(KEY_USERNAME, null)
        set(value) {
            prefs.edit().apply {
                if (value.isNullOrBlank()) remove(KEY_USERNAME) else putString(KEY_USERNAME, value)
            }.apply()
        }

    /**
     * 用户在本机设的后端地址。为空表示「没设过」，此时用编译期默认
     * （`BuildConfig.DATAPP_BASE_URL`，源头是根 `gradle.properties` 的 `datapp.baseUrl`）。
     *
     * 存本机是因为局域网 IP 由 DHCP 分配、换网络就变，编进 APK 必然过期一次。
     */
    var baseUrl: String?
        get() = prefs.getString(KEY_BASE_URL, null)
        set(value) {
            prefs.edit().apply {
                if (value.isNullOrBlank()) remove(KEY_BASE_URL) else putString(KEY_BASE_URL, value)
            }.apply()
        }

    /**
     * 清会话。**只清会话，不清 [baseUrl]** —— 后端地址是设备配置，不是会话状态，
     * 登出把它一起抹掉，用户还得重新填一遍。
     */
    fun clear() {
        prefs.edit().remove(KEY_COOKIE).remove(KEY_USERNAME).apply()
    }

    private companion object {
        const val KEY_COOKIE = "admin_cookie"
        const val KEY_USERNAME = "last_username"
        const val KEY_BASE_URL = "base_url"
    }
}
