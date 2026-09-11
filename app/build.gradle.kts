import java.util.Properties

plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.kotlin.compose)
}

// 机器本地配置（local.properties 不入版本控制，与 sdk.dir 同级）。
// 缺省为空 → 请求不带对应头 → 服务端 401，故障显式可见，不静默降级。
val localProps: Properties = Properties().apply {
    val f = rootProject.file("local.properties")
    if (f.exists()) f.inputStream().use { load(it) }
}

// 移动端设备令牌（datapp.appToken）：app 用它免账号密码启动，可读可写，
// 但服务端已把采集模块（/collection-runs、/browser/*）对它关死。
// 未配置 → app 回落登录页。
val datappAppToken: String = localProps.getProperty("datapp.appToken").orEmpty()

android {
    namespace = "com.example.datapp"
    compileSdk {
        version = release(37)
    }

    defaultConfig {
        applicationId = "com.example.datapp"
        minSdk = 26
        targetSdk = 37
        versionCode = 1
        versionName = "1.0"

        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"

        // 后端地址来自根项目 gradle.properties 的 datapp.baseUrl，
        // 兜底 127.0.0.1（仅模拟器 / 本机测试可用，真机必须改 gradle.properties）。
        buildConfigField(
            "String",
            "DATAPP_BASE_URL",
            "\"${project.findProperty("datapp.baseUrl") ?: "http://127.0.0.1:8000"}\"",
        )

        // 移动端设备令牌，来自 local.properties 的 datapp.appToken（见文件顶部）。
        buildConfigField("String", "DATAPP_APP_TOKEN", "\"$datappAppToken\"")
    }

    signingConfigs {
        create("localDebug") {
            storeFile = file("debug.keystore")
            storePassword = "android"
            keyAlias = "androiddebugkey"
            keyPassword = "android"
        }
    }

    buildTypes {
        debug {
            // debug.keystore 不入库（app/.gitignore）；本机有就用本机，没有则退回 AGP 默认调试签名。
            if (file("debug.keystore").exists()) {
                signingConfig = signingConfigs.getByName("localDebug")
            }
        }
        release {
            optimization {
                enable = false
            }
        }
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_11
        targetCompatibility = JavaVersion.VERSION_11
    }
    buildFeatures {
        compose = true
        buildConfig = true
    }
    testOptions {
        unitTests.isReturnDefaultValues = true
    }
}

dependencies {
    implementation(platform(libs.androidx.compose.bom))
    implementation(libs.androidx.activity.compose)
    implementation(libs.androidx.compose.material3)
    implementation(libs.androidx.compose.ui)
    implementation(libs.androidx.compose.ui.graphics)
    implementation(libs.androidx.core.ktx)
    implementation("io.coil-kt:coil-compose:2.6.0")
    testImplementation(libs.junit)
    testImplementation("org.json:json:20231013")
    // androidTest 只留 ExampleInstrumentedTest 真正用到的 runner（androidx-junit）。
    // Compose UI 测试 / Espresso 现在没有用例，需要时再加回来，别让每个构建都去解析它们。
    androidTestImplementation(libs.androidx.junit)
    // ui-tooling 只在 debug 包，供 IDE 预览渲染；@Preview 注解本身没用到，所以不引 ui-tooling-preview。
    debugImplementation(libs.androidx.compose.ui.tooling)
}
