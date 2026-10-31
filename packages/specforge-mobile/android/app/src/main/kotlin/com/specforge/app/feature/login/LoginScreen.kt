package com.specforge.app.feature.login

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.safeDrawingPadding
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import com.specforge.app.R
import com.specforge.app.core.ui.StateContainer
import com.specforge.app.core.ui.UiState
import com.specforge.app.feature.Screens

@Composable
fun LoginRoute(vm: LoginViewModel, onDone: () -> Unit) {
    val state by vm.state.collectAsState()
    LaunchedEffect(state.done) { if (state.done) onDone() }
    LoginScreen(state, vm::onLogin, vm::onPassword, vm::submit)
}

/** Stateless-экран: его же рендерит скриншот-тест. */
@Composable
fun LoginScreen(
    state: LoginUiState,
    onLogin: (String) -> Unit,
    onPassword: (String) -> Unit,
    onSubmit: () -> Unit,
) {
    Scaffold { padding ->
        StateContainer(Screens.LOGIN, UiState.Ready(state), onRetry = {}, modifier = Modifier.padding(padding)) { s ->
            Column(
                Modifier.fillMaxSize().safeDrawingPadding().padding(24.dp),
                verticalArrangement = Arrangement.spacedBy(16.dp, Alignment.CenterVertically),
            ) {
                Text(stringResource(R.string.login_title), style = MaterialTheme.typography.headlineMedium)
                OutlinedTextField(
                    value = s.login,
                    onValueChange = onLogin,
                    label = { Text(stringResource(R.string.login_field_login)) },
                    singleLine = true,
                    modifier = Modifier.fillMaxWidth().testTag("login-field-login"),
                )
                OutlinedTextField(
                    value = s.password,
                    onValueChange = onPassword,
                    label = { Text(stringResource(R.string.login_field_password)) },
                    singleLine = true,
                    visualTransformation = PasswordVisualTransformation(),
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password),
                    modifier = Modifier.fillMaxWidth().testTag("login-field-password"),
                )
                if (s.error != null) {
                    Text(s.error, color = MaterialTheme.colorScheme.error, modifier = Modifier.testTag("login-error"))
                }
                Button(
                    onClick = onSubmit,
                    enabled = s.canSubmit,
                    modifier = Modifier.fillMaxWidth().testTag("login-submit"),
                ) { Text(stringResource(if (s.busy) R.string.login_busy else R.string.login_submit)) }
            }
        }
    }
}
