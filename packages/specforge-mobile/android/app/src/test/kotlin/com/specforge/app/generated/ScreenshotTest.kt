// СГЕНЕРИРОВАНО из UX-спеки (tools/android_gate/screens.py) — не править, перезаписывается.
package com.specforge.app.generated

import androidx.compose.runtime.Composable
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onRoot
import androidx.test.ext.junit.runners.AndroidJUnit4
import com.github.takahirom.roborazzi.captureRoboImage
import com.specforge.app.core.ui.theme.AppTheme
import com.specforge.app.feature.login.LoginScreenSample
import com.specforge.app.feature.session.SessionScreenSample
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.annotation.Config
import org.robolectric.annotation.GraphicsMode
import java.io.File

/** Снимки для VLM-ревьюера: screenshots/<screenId>--<state>--mobile.png (задача recordRoborazziDebug). */
@RunWith(AndroidJUnit4::class)
@GraphicsMode(GraphicsMode.Mode.NATIVE)
@Config(sdk = [35], qualifiers = "w411dp-h891dp-xxhdpi")
class ScreenshotTest {
    @get:Rule val compose = createComposeRule()

    private val dir = File(System.getProperty("specforge.screenshotDir") ?: "build/screenshots")

    private fun shot(screenId: String, state: String, content: @Composable () -> Unit) {
        compose.setContent { AppTheme(dark = false) { content() } }
        compose.onRoot().captureRoboImage(File(dir, "$screenId--$state--mobile.png").path)
    }

    @Test fun login_ready() = shot("login", "ready") { LoginScreenSample("ready") }
    @Test fun login_error() = shot("login", "error") { LoginScreenSample("error") }
    @Test fun session_ready() = shot("session", "ready") { SessionScreenSample("ready") }
    @Test fun session_loading() = shot("session", "loading") { SessionScreenSample("loading") }
    @Test fun session_empty() = shot("session", "empty") { SessionScreenSample("empty") }
    @Test fun session_error() = shot("session", "error") { SessionScreenSample("error") }
    @Test fun session_forbidden() = shot("session", "forbidden") { SessionScreenSample("forbidden") }
}
