package com.specforge.app.feature

import android.Manifest
import android.os.Build
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.platform.LocalContext
import androidx.lifecycle.viewmodel.compose.viewModel
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.rememberNavController
import kotlinx.coroutines.flow.map
import com.specforge.app.core.di.AppContainer
import com.specforge.app.core.tracking.TrackingService
import com.specforge.app.feature.login.LoginRoute
import com.specforge.app.feature.login.LoginViewModel
import com.specforge.app.feature.session.SessionRoute
import com.specforge.app.feature.session.SessionViewModel

/** Корень навигации. id маршрутов = screenId из UX-спеки (screen-spec.android.json). */
object Screens {
    const val LOGIN = "login"
    const val SESSION = "session"
}

@Composable
fun AppRoot(container: AppContainer) {
    val nav = rememberNavController()
    val token by container.tokenStore.token.collectAsState()
    val context = LocalContext.current

    val permissions = buildList {
        add(Manifest.permission.ACCESS_FINE_LOCATION)
        if (Build.VERSION.SDK_INT >= 33) add(Manifest.permission.POST_NOTIFICATIONS)
    }.toTypedArray()
    val requestPermissions = rememberLauncherForActivityResult(ActivityResultContracts.RequestMultiplePermissions()) { granted ->
        if (granted[Manifest.permission.ACCESS_FINE_LOCATION] == true) TrackingService.start(context)
    }

    NavHost(nav, startDestination = if (token == null) Screens.LOGIN else Screens.SESSION) {
        composable(Screens.LOGIN) {
            val vm = viewModel { LoginViewModel(container.feature.repository) }
            LoginRoute(vm) {
                nav.navigate(Screens.SESSION) { popUpTo(Screens.LOGIN) { inclusive = true } }
            }
        }
        composable(Screens.SESSION) {
            val vm = viewModel { SessionViewModel(
                    container.feature.repository,
                    container.config.timeScale,
                    TrackingService.state.map { it.running },
                ) }
            SessionRoute(
                vm = vm,
                onStartTracking = { requestPermissions.launch(permissions) },
                onStopTracking = { TrackingService.stop(context) },
            )
        }
    }
}
