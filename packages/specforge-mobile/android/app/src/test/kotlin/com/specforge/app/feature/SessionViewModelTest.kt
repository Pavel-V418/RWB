package com.specforge.app.feature

import com.specforge.app.core.network.ApiResult
import com.specforge.app.core.ui.UiState
import com.specforge.app.feature.session.SessionScreenData
import com.specforge.app.feature.session.SessionViewModel
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.test.StandardTestDispatcher
import kotlinx.coroutines.test.TestScope
import kotlinx.coroutines.test.advanceTimeBy
import kotlinx.coroutines.test.resetMain
import kotlinx.coroutines.test.runCurrent
import kotlinx.coroutines.test.runTest
import kotlinx.coroutines.test.setMain
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

@OptIn(ExperimentalCoroutinesApi::class)
class SessionViewModelTest {
    private val dispatcher = StandardTestDispatcher()
    private val repo = FakeSessionRepository()

    @Before fun setUp() = Dispatchers.setMain(dispatcher)
    @After fun tearDown() = Dispatchers.resetMain()

    private fun TestScope.vm(): SessionViewModel =
        SessionViewModel(repo, timeScale = 10, tracking = MutableStateFlow(false), newKey = { "key-1" })
            .also { runCurrent() }

    private fun SessionViewModel.ready(): SessionScreenData = (state.value as UiState.Ready).data

    @Test
    fun `загрузка — зона показана, сессии нет`() = runTest(dispatcher) {
        val vm = vm()
        assertEquals("Казанская пл. дом 2", vm.ready().status.zone?.title)
        assertEquals(null, vm.ready().status.session)
    }

    @Test
    fun `нет машины — состояние empty, нет прав — forbidden, сбой — error`() = runTest(dispatcher) {
        repo.refreshResult = ApiResult.Fail(404, "")
        assertTrue(vm().state.value is UiState.Empty)
        repo.refreshResult = ApiResult.Fail(403, "")
        assertTrue(vm().state.value is UiState.Forbidden)
        repo.refreshResult = ApiResult.Fail(null, "Нет связи с сервером")
        assertTrue(vm().state.value is UiState.Error)
    }

    @Test
    fun `старт сессии передаёт зону, длительность и ключ идемпотентности`() = runTest(dispatcher) {
        val vm = vm()
        vm.start(30)
        runCurrent()
        assertEquals(listOf(Triple(FakeSessionRepository.ZONE.id, 30, "key-1")), repo.startCalls)
        assertEquals(FakeSessionRepository.SESSION.id, vm.ready().status.session?.id)
    }

    @Test
    fun `повторное нажатие во время запроса игнорируется`() = runTest(dispatcher) {
        val vm = vm()
        repo.startGate = CompletableDeferred()
        vm.start(15)
        runCurrent()
        assertTrue(vm.ready().busy)
        vm.start(15)
        runCurrent()
        assertEquals(1, repo.startCalls.size)
        repo.startGate!!.complete(Unit)
        runCurrent()
        assertEquals(false, vm.ready().busy)
    }

    @Test
    fun `таймер идёт с ускорением TIME_SCALE`() = runTest(dispatcher) {
        repo.refreshResult = ApiResult.Ok(FakeSessionRepository.STATUS_WITH_SESSION)
        val vm = vm()
        assertEquals(1500L, vm.ready().remainingSeconds)
        advanceTimeBy(3_001)
        assertEquals(1470L, vm.ready().remainingSeconds)
    }

    @Test
    fun `ответ на отправку координат обновляет экран`() = runTest(dispatcher) {
        val vm = vm()
        repo.live.value = FakeSessionRepository.STATUS_WITH_SESSION
        runCurrent()
        assertEquals(FakeSessionRepository.SESSION.id, vm.ready().status.session?.id)
    }
}
