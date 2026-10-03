plugins { id("com.android.application") }

val repo: String = System.getenv("GITHUB_REPOSITORY") ?: ""
val bakedAk: String = System.getenv("BAIDU_AK") ?: ""
val ksFile: String? = System.getenv("KEYSTORE_FILE")

android {
    namespace = "cn.gzbib.map"
    compileSdk = 35

    defaultConfig {
        applicationId = "cn.gzbib.map"
        minSdk = 26
        targetSdk = 34
        versionCode = (System.getenv("GITHUB_RUN_NUMBER") ?: "1").toInt()
        versionName = "1." + (System.getenv("GITHUB_RUN_NUMBER") ?: "0")
        buildConfigField("String", "REPO", "\"$repo\"")
        buildConfigField("String", "BAIDU_AK", "\"$bakedAk\"")
    }

    signingConfigs {
        create("release") {
            if (ksFile != null && file(ksFile).exists()) {
                storeFile = file(ksFile)
                storePassword = System.getenv("KEYSTORE_PASSWORD")
                keyAlias = System.getenv("KEY_ALIAS")
                keyPassword = System.getenv("KEY_PASSWORD")
            }
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            signingConfig = if (ksFile != null && file(ksFile).exists())
                signingConfigs.getByName("release") else signingConfigs.getByName("debug")
        }
    }

    buildFeatures { buildConfig = true }

    // 网页与名单直接打包进 App，作为离线兜底
    sourceSets["main"].assets.srcDirs("../web", "../data")

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
}

dependencies {
    implementation("androidx.webkit:webkit:1.12.1")
}
