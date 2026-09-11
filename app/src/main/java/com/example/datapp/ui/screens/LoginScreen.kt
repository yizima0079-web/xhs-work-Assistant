package com.example.datapp.ui.screens

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.example.datapp.data.ApiResult
import com.example.datapp.data.DatappRepository
import com.example.datapp.ui.components.BaseUrlDialog
import com.example.datapp.ui.components.ErrorBanner
import com.example.datapp.ui.components.GlassCard
import com.example.datapp.ui.theme.DatappBlueDeep
import com.example.datapp.ui.theme.DatappCyan
import com.example.datapp.ui.theme.DatappInk
import com.example.datapp.ui.theme.DatappMuted
import com.example.datapp.ui.theme.ThemeHomeAccent
import com.example.datapp.ui.theme.ThemeHomeBg
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/**
 * 启动即登录。
 *
 * 为什么必须先登录：未配设备令牌时没有任何凭证，服务端一律 401，
 * 审核改判 / 触发分析 / 生成报告 / 向量化这些**写操作必须带管理员会话 Cookie**。
 * 不登录的话 app 就只能看，不能操作 —— 与本次目标「继承前端全部功能」不符。
 */
@Composable
fun LoginScreen(
    repository: DatappRepository,
    initialUsername: String,
    onLoggedIn: (String) -> Unit,
    onSaveBaseUrl: (String) -> Unit,
) {
    var username by remember { mutableStateOf(initialUsername.ifBlank { "admin" }) }
    var password by remember { mutableStateOf("") }
    var error by remember { mutableStateOf<String?>(null) }
    var busy by remember { mutableStateOf(false) }
    var editingBaseUrl by remember { mutableStateOf(false) }
    val scope = rememberCoroutineScope()

    fun submit() {
        if (busy) return
        error = null
        busy = true
        scope.launch {
            val result = withContext(Dispatchers.IO) {
                repository.login(username.trim(), password)
            }
            busy = false
            when (result) {
                is ApiResult.Success -> onLoggedIn(username.trim())
                is ApiResult.Error -> error = result.message
            }
        }
    }

    Box(
        modifier = Modifier
            .fillMaxSize()
            .background(
                Brush.verticalGradient(
                    listOf(ThemeHomeBg, Color.White, ThemeHomeBg),
                )
            )
            .imePadding(),
        contentAlignment = Alignment.Center,
    ) {
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .verticalScroll(rememberScrollState())
                .padding(PaddingValues(horizontal = 28.dp, vertical = 40.dp)),
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            Box(
                Modifier
                    .size(64.dp)
                    .clip(CircleShape)
                    .background(Brush.linearGradient(listOf(ThemeHomeAccent, DatappCyan))),
                contentAlignment = Alignment.Center,
            ) {
                Text("⌘", color = Color.White, fontSize = 28.sp, fontWeight = FontWeight.Bold)
            }
            Spacer(Modifier.height(16.dp))
            Text("datapp 看板", color = DatappInk, fontSize = 22.sp, fontWeight = FontWeight.Bold)
            Spacer(Modifier.height(6.dp))
            Text("登录后可读写审核、分析、报告与知识库", color = DatappMuted, fontSize = 12.sp)

            Spacer(Modifier.height(26.dp))

            GlassCard(modifier = Modifier.fillMaxWidth(), shape = RoundedCornerShape(22.dp)) {
                Text("管理员账号", color = DatappMuted, fontSize = 11.sp, fontWeight = FontWeight.SemiBold)
                Spacer(Modifier.height(8.dp))
                OutlinedTextField(
                    value = username,
                    onValueChange = { username = it },
                    modifier = Modifier.fillMaxWidth(),
                    singleLine = true,
                    enabled = !busy,
                    shape = RoundedCornerShape(14.dp),
                    placeholder = { Text("admin", color = DatappMuted, fontSize = 13.sp) },
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Text, imeAction = ImeAction.Next),
                    colors = loginFieldColors(),
                )
                Spacer(Modifier.height(12.dp))
                Text("密码", color = DatappMuted, fontSize = 11.sp, fontWeight = FontWeight.SemiBold)
                Spacer(Modifier.height(8.dp))
                OutlinedTextField(
                    value = password,
                    onValueChange = { password = it },
                    modifier = Modifier.fillMaxWidth(),
                    singleLine = true,
                    enabled = !busy,
                    shape = RoundedCornerShape(14.dp),
                    placeholder = { Text("••••••", color = DatappMuted, fontSize = 13.sp) },
                    visualTransformation = PasswordVisualTransformation(),
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password, imeAction = ImeAction.Done),
                    colors = loginFieldColors(),
                )

                if (error != null) {
                    Spacer(Modifier.height(12.dp))
                    ErrorBanner(error!!)
                }

                Spacer(Modifier.height(18.dp))
                Button(
                    onClick = { submit() },
                    modifier = Modifier.fillMaxWidth().height(48.dp),
                    enabled = !busy && username.isNotBlank() && password.isNotEmpty(),
                    shape = RoundedCornerShape(14.dp),
                    colors = ButtonDefaults.buttonColors(containerColor = ThemeHomeAccent),
                ) {
                    Text(
                        text = if (busy) "登录中…" else "登录",
                        fontSize = 14.sp,
                        fontWeight = FontWeight.Bold,
                        color = Color.White,
                    )
                }

                Spacer(Modifier.height(6.dp))
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text("后端地址", color = DatappMuted, fontSize = 10.5.sp)
                    Spacer(Modifier.width(6.dp))
                    Text(
                        repository.baseUrl,
                        color = DatappBlueDeep,
                        fontSize = 10.5.sp,
                        fontWeight = FontWeight.SemiBold,
                        modifier = Modifier.weight(1f),
                    )
                    if (error != null) {
                        TextButton(onClick = { submit() }, contentPadding = PaddingValues(0.dp)) {
                            Text("重试", color = ThemeHomeAccent, fontSize = 11.sp, fontWeight = FontWeight.SemiBold)
                        }
                    }
                }
            }

            Spacer(Modifier.height(14.dp))
            TextButton(
                onClick = { editingBaseUrl = true },
                contentPadding = PaddingValues(0.dp),
            ) {
                Text(
                    "连不上？改服务器地址",
                    color = DatappBlueDeep,
                    fontSize = 11.sp,
                    fontWeight = FontWeight.SemiBold,
                )
            }
        }
    }

    // 这个入口**不是可选的**：Cookie 模式下地址若填错，冷启动必然停在这一页，
    // 而「我的」页要会话就绪后才可达 —— 没有它，用户就被锁死在登录页了。
    if (editingBaseUrl) {
        BaseUrlDialog(
            current = repository.baseUrl,
            accentColor = ThemeHomeAccent,
            onDismiss = { editingBaseUrl = false },
            onSave = {
                editingBaseUrl = false
                onSaveBaseUrl(it)
            },
        )
    }
}

@Composable
private fun loginFieldColors() = OutlinedTextFieldDefaults.colors(
    focusedContainerColor = Color.White,
    unfocusedContainerColor = Color.White.copy(alpha = 0.85f),
    focusedBorderColor = ThemeHomeAccent.copy(alpha = 0.6f),
    unfocusedBorderColor = Color.White,
    disabledContainerColor = Color.White.copy(alpha = 0.6f),
    disabledBorderColor = Color.White,
)
