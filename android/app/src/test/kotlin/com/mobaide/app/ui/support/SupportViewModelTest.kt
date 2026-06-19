package com.mobaide.app.ui.support

import app.cash.turbine.test
import com.mobaide.app.data.auth.DeviceIdentityStore
import com.mobaide.app.data.auth.UserIdentityStore
import com.mobaide.app.testing.MainDispatcherRule
import io.mockk.coEvery
import io.mockk.every
import io.mockk.mockk
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.test.advanceUntilIdle
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Rule
import org.junit.Test

/**
 * Unit tests for [SupportViewModel]. The `init` block launches a coroutine on
 * `viewModelScope` that reads the (suspend) device id, the device name, and the
 * nullable signed-in email into [SupportUiState]; under a
 * [StandardTestDispatcher] that coroutine only runs once the test yields, so
 * each case advances the dispatcher before asserting the populated state.
 */
@OptIn(ExperimentalCoroutinesApi::class)
class SupportViewModelTest {

    @get:Rule
    val mainDispatcherRule = MainDispatcherRule()

    private fun stubDeviceIdentityStore(
        deviceId: String = "device-uuid-1",
        deviceName: String = "Pixel 8 Pro",
    ): DeviceIdentityStore = mockk {
        coEvery { getOrCreateDeviceId() } returns deviceId
        every { getDeviceName() } returns deviceName
    }

    private fun stubUserIdentityStore(email: String?): UserIdentityStore = mockk {
        every { getUserEmail() } returns email
    }

    @Test
    fun initialState_isEmptyBeforeInitCoroutineRuns() = runTest {
        val vm = SupportViewModel(
            stubDeviceIdentityStore(),
            stubUserIdentityStore(email = "alice@example.com"),
        )

        // Default state is observable before the StandardTestDispatcher yields.
        val initial = vm.uiState.value
        assertEquals("", initial.deviceId)
        assertEquals("", initial.deviceName)
        assertNull(initial.userEmail)
    }

    @Test
    fun init_populatesState_withDeviceIdDeviceNameAndEmail_whenSignedIn() = runTest {
        val vm = SupportViewModel(
            stubDeviceIdentityStore(deviceId = "id-42", deviceName = "Galaxy S24"),
            stubUserIdentityStore(email = "bob@example.com"),
        )

        advanceUntilIdle()

        val state = vm.uiState.value
        assertEquals("id-42", state.deviceId)
        assertEquals("Galaxy S24", state.deviceName)
        assertEquals("bob@example.com", state.userEmail)
    }

    @Test
    fun init_populatesDeviceFields_butLeavesEmailNull_whenSignedOut() = runTest {
        val vm = SupportViewModel(
            stubDeviceIdentityStore(deviceId = "id-7", deviceName = "Android device"),
            stubUserIdentityStore(email = null),
        )

        advanceUntilIdle()

        val state = vm.uiState.value
        assertEquals("id-7", state.deviceId)
        assertEquals("Android device", state.deviceName)
        assertNull("signed-out reporters must keep a null account email", state.userEmail)
    }

    @Test
    fun uiState_emitsDefaultThenPopulated() = runTest {
        val vm = SupportViewModel(
            stubDeviceIdentityStore(deviceId = "id-99", deviceName = "Pixel 9"),
            stubUserIdentityStore(email = "carol@example.com"),
        )

        vm.uiState.test {
            assertEquals(SupportUiState(), awaitItem())

            advanceUntilIdle()

            val populated = awaitItem()
            assertEquals("id-99", populated.deviceId)
            assertEquals("Pixel 9", populated.deviceName)
            assertEquals("carol@example.com", populated.userEmail)
            cancelAndIgnoreRemainingEvents()
        }
    }
}
