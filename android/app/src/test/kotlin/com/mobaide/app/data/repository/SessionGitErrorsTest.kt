package com.mobaide.app.data.repository

import com.mobaide.app.data.network.ApiException
import kotlinx.serialization.SerializationException
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertSame
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.IOException

/**
 * The status-code → [SessionGitException] mapping the git Changes screens
 * branch on. The two 404s share a code and differ only in FastAPI's `detail`
 * string, so that string is the whole discriminator.
 */
class SessionGitErrorsTest {

    private fun api(code: Int, body: String) = ApiException(code, body)

    @Test
    fun sessionNotFound404_isSessionGone() {
        val cause = api(404, """{"detail":"Session 'abc-123' not found"}""")
        val mapped = mapSessionGitError(cause)
        assertTrue(mapped is SessionGitException.SessionGone)
        assertSame(cause, mapped.cause)
    }

    @Test
    fun fastApiNotFound404_isAgentOutdated() {
        assertTrue(
            mapSessionGitError(api(404, """{"detail":"Not Found"}""")) is SessionGitException.AgentOutdated,
        )
    }

    @Test
    fun relayUnknownRoute404_isAgentOutdated() {
        val body = """{"detail":"Unknown proxy route: /api/sessions/s1/git/status"}"""
        assertTrue(mapSessionGitError(api(404, body)) is SessionGitException.AgentOutdated)
    }

    @Test
    fun aBodilessOrUnparseable404_isAgentOutdated() {
        assertTrue(mapSessionGitError(api(404, "")) is SessionGitException.AgentOutdated)
        assertTrue(mapSessionGitError(api(404, "<html>nope</html>")) is SessionGitException.AgentOutdated)
    }

    @Test
    fun anObjectDetailWithACode_isGitError_carryingTheMessage() {
        val cause = api(504, """{"detail":{"code":"git_timeout","message":"git status timed out"}}""")
        val mapped = mapSessionGitError(cause) as SessionGitException.GitError
        assertEquals(SessionGitException.CODE_GIT_TIMEOUT, mapped.code)
        assertEquals("git status timed out", mapped.detailMessage)
        assertSame(cause, mapped.cause)
    }

    @Test
    fun anObjectDetailWithoutAMessage_isGitError_withANullMessage() {
        val mapped = mapSessionGitError(api(400, """{"detail":{"code":"bad_path"}}"""))
            as SessionGitException.GitError
        assertEquals(SessionGitException.CODE_BAD_PATH, mapped.code)
        assertNull(mapped.detailMessage)
    }

    @Test
    fun aStringDetailOnANon404_staysAnApiException() {
        // The relay's "Agent is offline" 503 is not a git failure.
        val cause = api(503, """{"detail":"Agent is offline"}""")
        assertSame(cause, mapSessionGitError(cause))
    }

    @Test
    fun anObjectDetailWithoutACode_staysAnApiException() {
        val cause = api(409, """{"detail":{"busy_by":[]}}""")
        assertSame(cause, mapSessionGitError(cause))
    }

    @Test
    fun ioAndDecodeFailures_areNetwork_withTheCause() {
        val io = IOException("timeout")
        val mappedIo = mapSessionGitError(io)
        assertTrue(mappedIo is SessionGitException.Network)
        assertSame(io, mappedIo.cause)
        assertEquals("timeout", mappedIo.message)

        val decode = SerializationException("bad json")
        assertTrue(mapSessionGitError(decode) is SessionGitException.Network)
    }

    @Test
    fun otherThrowables_passThroughUntouched() {
        val runtime = RuntimeException("oops")
        assertSame(runtime, mapSessionGitError(runtime))

        val already = SessionGitException.AgentOutdated(api(404, ""))
        assertSame(already, mapSessionGitError(already))
    }

    @Test
    fun extractDetail_readsStringAndObjectDetails() {
        assertEquals("Not Found", extractDetail("""{"detail":"Not Found"}"""))

        val detail = extractDetail("""{"detail":{"code":"git_error","message":"boom"}}""")
        assertTrue(detail is JsonObject)
        assertEquals("git_error", (detail as JsonObject)["code"]?.jsonPrimitive?.content)
    }

    @Test
    fun extractDetail_rejectsEveryOtherShape() {
        assertNull(extractDetail(""))
        assertNull(extractDetail("   "))
        assertNull(extractDetail("not json {"))
        assertNull(extractDetail("[]"))
        assertNull(extractDetail("""{"message":"no detail key"}"""))
        assertNull(extractDetail("""{"detail":["a","b"]}"""))
        assertNull(extractDetail("""{"detail":null}"""))
    }
}

class SessionGitErrorsJobRoutesTest {

    private fun api(code: Int, body: String) = ApiException(code, body)

    @Test
    fun aCoded404_isAGitError_neverAnOutdatedAgent() {
        // `GET …/git/jobs/{id}` answers `{"detail":{"code":"job_not_found"}}`;
        // read as AgentOutdated it would end the status feed and hide every
        // mutating control, so the coded object wins over the 404 branches.
        val mapped = mapSessionGitError(api(404, """{"detail":{"code":"job_not_found"}}"""))
            as SessionGitException.GitError
        assertEquals(SessionGitException.CODE_JOB_NOT_FOUND, mapped.code)
        assertNull(mapped.detailMessage)
    }

    @Test
    fun jobRunning409_carriesTheActiveJobId() {
        val mapped = mapSessionGitError(
            api(409, """{"detail":{"code":"job_running","message":"ab12cd34"}}"""),
        ) as SessionGitException.GitError
        assertEquals(SessionGitException.CODE_JOB_RUNNING, mapped.code)
        assertEquals("ab12cd34", mapped.detailMessage)
    }

    @Test
    fun aCoded404WithoutAString_stillNeverReadsAsSessionGone() {
        val mapped = mapSessionGitError(api(404, """{"detail":{"code":"job_not_found","message":null}}"""))
        assertTrue(mapped is SessionGitException.GitError)
    }
}
