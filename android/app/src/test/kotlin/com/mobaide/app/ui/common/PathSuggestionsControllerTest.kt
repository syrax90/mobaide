package com.mobaide.app.ui.common

import com.mobaide.app.data.auth.TokenStore
import com.mobaide.app.data.network.RestApi
import com.mobaide.app.data.network.dto.FsDirsResponse
import com.mobaide.app.data.network.dto.VersionInfoResponse
import com.mobaide.app.data.repository.PathSuggestionRepository
import io.mockk.coEvery
import io.mockk.coVerify
import io.mockk.every
import io.mockk.mockk
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.test.TestScope
import kotlinx.coroutines.test.advanceTimeBy
import kotlinx.coroutines.test.runCurrent
import kotlinx.coroutines.test.runTest
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import org.junit.Assert.assertEquals
import org.junit.Test

/**
 * [PathSuggestionsController] over a real [PathSuggestionRepository] whose
 * [RestApi] is mocked at the method level: the 200 ms debounce on the test
 * scheduler, one listing per directory while the user types inside it, the
 * truncated-listing `q` follow-up, and the focus / capability gates.
 */
@OptIn(ExperimentalCoroutinesApi::class)
class PathSuggestionsControllerTest {

    private val restApi = mockk<RestApi>()
    private val tokenStore = mockk<TokenStore>().also {
        every { it.getAgentId() } returns "agent-1"
        every { it.getAgentUrl() } returns null
    }

    private fun version(fs: String?) = VersionInfoResponse(
        name = "mobaide_agent",
        server_version = "1.4.0",
        protocol_versions = listOf(7),
        min_client_version = 1,
        capabilities = Json.parseToJsonElement(if (fs == null) "{}" else """{"fs":$fs}""") as JsonObject,
    )

    private fun answer(dir: String, q: String?, dirs: List<String>, truncated: Boolean = false) {
        coEvery { restApi.listDirs(dir, q) } returns
            FsDirsResponse(dir.replace(Regex("^~/"), "/home/me/"), dirs, truncated)
    }

    private fun TestScope.focusedController(supported: Boolean = true): PathSuggestionsController {
        coEvery { restApi.getAgentVersion() } returns version(if (supported) """{"dirs":true}""" else null)
        val controller = PathSuggestionsController(
            PathSuggestionRepository(restApi, tokenStore) { testScheduler.currentTime },
            backgroundScope,
        )
        controller.onFocusChange(true)
        runCurrent()
        return controller
    }

    private fun TestScope.type(controller: PathSuggestionsController, text: String) {
        controller.onTextChange(text)
        runCurrent()
        advanceTimeBy(200)
        runCurrent()
    }

    @Test
    fun listsADirectoryOnceAfterTheDebounce_andFiltersItLocallyWhileTyping() = runTest {
        answer("/", null, listOf(".hidden", "Users", "usr", "opt"))
        val controller = focusedController()

        controller.onTextChange("/")
        runCurrent()
        advanceTimeBy(199)
        runCurrent()
        coVerify(exactly = 0) { restApi.listDirs(any(), any()) }

        advanceTimeBy(1)
        runCurrent()
        assertEquals(PathSuggestionsUi(listOf("Users", "usr", "opt"), 0, "/"), controller.state.value)

        type(controller, "/us")
        type(controller, "/Use")

        coVerify(exactly = 1) { restApi.listDirs(any(), any()) }
        assertEquals(listOf("Users"), controller.state.value.names)
    }

    @Test
    fun onlyTheLastDirectoryTypedWithinTheDebounceIsListed() = runTest {
        answer("/b/", null, listOf("x"))
        val controller = focusedController()

        controller.onTextChange("/a/")
        runCurrent()
        advanceTimeBy(100)
        type(controller, "/b/")

        coVerify(exactly = 0) { restApi.listDirs("/a/", any()) }
        coVerify(exactly = 1) { restApi.listDirs("/b/", null) }
        assertEquals(listOf("x"), controller.state.value.names)
    }

    @Test
    fun reAsksATruncatedListingWithTheTypedFragmentAsQ() = runTest {
        answer("/big/", null, listOf("aaa"), truncated = true)
        answer("/big/", "pro", listOf("project"))
        val controller = focusedController()

        type(controller, "/big/")
        type(controller, "/big/pro")

        coVerify(exactly = 1) { restApi.listDirs("/big/", "pro") }
        assertEquals(listOf("project"), controller.state.value.names)
    }

    @Test
    fun showsEightAndCountsTheRest() = runTest {
        answer("/", null, (0 until 10).map { "d$it" })
        val controller = focusedController()

        type(controller, "/")

        assertEquals(8, controller.state.value.names.size)
        assertEquals(2, controller.state.value.overflow)
    }

    @Test
    fun acceptWritesTheAgentExpandedPath_forATildeQuery() = runTest {
        answer("~/", null, listOf("code"))
        val controller = focusedController()

        type(controller, "~/")

        assertEquals("/home/me/code/", controller.accept("code"))
    }

    @Test
    fun showsAndAsksNothingOnceTheFieldLosesFocus() = runTest {
        answer("/", null, listOf("srv"))
        val controller = focusedController()
        type(controller, "/")

        controller.onFocusChange(false)
        type(controller, "/s")

        assertEquals(PathSuggestionsUi.None, controller.state.value)
    }

    @Test
    fun asksNothingOfAnAgentWithoutTheCapability() = runTest {
        val controller = focusedController(supported = false)

        type(controller, "/")

        coVerify(exactly = 0) { restApi.listDirs(any(), any()) }
        assertEquals(PathSuggestionsUi.None, controller.state.value)
    }

    @Test
    fun asksNothingForARelativePath() = runTest {
        val controller = focusedController()

        type(controller, "code/")

        coVerify(exactly = 0) { restApi.listDirs(any(), any()) }
        assertEquals(PathSuggestionsUi.None, controller.state.value)
    }
}
