package com.example.datapp.ui.theme

import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color

private val LightColorScheme = lightColorScheme(
    primary = DatappBlue,
    onPrimary = Color.White,
    secondary = DatappCyan,
    tertiary = DatappViolet,
    background = DatappBackground,
    onBackground = DatappInk,
    surface = Color.White,
    onSurface = DatappInk,
    surfaceVariant = DatappSurfaceSoft,
    onSurfaceVariant = DatappMuted,
    outline = DatappLine,
)

@Composable
fun DatappTheme(content: @Composable () -> Unit) {
    MaterialTheme(
        colorScheme = LightColorScheme,
        typography = Typography,
        content = content,
    )
}
