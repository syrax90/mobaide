package com.mobaide.app.data.network.dto

import com.mobaide.app.testing.ProtocolFixtures
import kotlinx.serialization.KSerializer
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The Android arm of the project git golden fixtures under
 * `tests/protocol_fixtures/dto/agent/`: the items list and the branch create /
 * checkout bodies. The request shapes are what this client sends, so they
 * must also encode back to the wire; every shape decodes from `wire` and, where
 * a case carries one, from `decoded_canonical` (the wire minus its nulls) to
 * the same value under the `RestApi` decoder configuration.
 */
class GitDtoFixtureTest {

    @Serializable
    private data class Case(
        val name: String,
        val wire: JsonObject,
        val decoded_canonical: JsonObject? = null,
    )

    @Serializable
    private data class Fixture(val type: String, val cases: List<Case>)

    private val json = Json { ignoreUnknownKeys = true; coerceInputValues = true }

    private fun fixture(type: String): Fixture {
        val loaded: Fixture = ProtocolFixtures.load("dto/agent/$type.json")
        assertEquals(type, loaded.type)
        assertTrue("$type has no cases", loaded.cases.isNotEmpty())
        return loaded
    }

    private fun <T> Fixture.decode(serializer: KSerializer<T>, name: String): T =
        json.decodeFromJsonElement(serializer, cases.first { it.name == name }.wire)

    private fun <T> Fixture.assertCanonicalDecodesLikeWire(serializer: KSerializer<T>) {
        for (case in cases) {
            val fromWire = json.decodeFromJsonElement(serializer, case.wire)
            val fromCanonical =
                json.decodeFromJsonElement(serializer, case.decoded_canonical ?: case.wire)
            assertEquals(case.name, fromWire, fromCanonical)
        }
    }

    @Test
    fun everyFixture_decodesFromWireAndCanonicalAlike() {
        fixture("GitItem").assertCanonicalDecodesLikeWire(GitItem.serializer())
        fixture("GitItemsResponse").assertCanonicalDecodesLikeWire(GitItemsResponse.serializer())
        fixture("GitBranchCreateRequest").assertCanonicalDecodesLikeWire(GitBranchCreateRequest.serializer())
        fixture("GitCheckoutRequest").assertCanonicalDecodesLikeWire(GitCheckoutRequest.serializer())
        fixture("GitBranchResponse").assertCanonicalDecodesLikeWire(GitBranchResponse.serializer())
    }

    @Test
    fun remoteBranchRow_isDeletableOnlyBehindDeleteRemote_notCurrent_butCanBeCheckedOut() {
        val remote = fixture("GitItem").decode(GitItem.serializer(), "remote_branch")

        assertEquals(GitItem.KIND_REMOTE_BRANCH, remote.kind)
        assertEquals("origin/feature/x", remote.branch)
        assertEquals("origin", remote.remote)
        assertEquals("Fix the login redirect", remote.subject)
        assertEquals("2026-09-16T10:11:12+02:00", remote.committed_at)
        assertFalse(remote.is_current)
        assertFalse(remote.isDeletable(deleteRemote = false))
        assertTrue(remote.isDeletable(deleteRemote = true))
        assertTrue(remote.canCheckOut)
    }

    @Test
    fun localRows_keepTheirTipAndTheirActions() {
        val fixture = fixture("GitItem")

        val current = fixture.decode(GitItem.serializer(), "full")
        val minimal = fixture.decode(GitItem.serializer(), "minimal_defaults_materialize")

        assertEquals("Fix the login redirect", current.subject)
        assertFalse(current.isDeletable(deleteRemote = false))
        assertFalse(current.isDeletable(deleteRemote = true))
        assertFalse(current.canCheckOut)
        assertNull(minimal.subject)
        assertNull(minimal.remote)
        assertTrue(minimal.isDeletable(deleteRemote = false))
        assertTrue(minimal.canCheckOut)
    }

    @Test
    fun requests_encodeBackToTheWire() {
        for (case in fixture("GitBranchCreateRequest").cases) {
            val decoded = json.decodeFromJsonElement(GitBranchCreateRequest.serializer(), case.wire)
            assertEquals(
                case.name,
                case.wire,
                json.encodeToJsonElement(GitBranchCreateRequest.serializer(), decoded),
            )
        }
        for (case in fixture("GitCheckoutRequest").cases) {
            val decoded = json.decodeFromJsonElement(GitCheckoutRequest.serializer(), case.wire)
            assertEquals(case.name, case.wire, json.encodeToJsonElement(GitCheckoutRequest.serializer(), decoded))
        }
    }

    @Test
    fun branchResponse_reportsWhatHappened() {
        val fixture = fixture("GitBranchResponse")

        val created = fixture.decode(GitBranchResponse.serializer(), "created_only")
        val checkedOut = fixture.decode(GitBranchResponse.serializer(), "checked_out_existing")

        assertEquals("feature/login", created.branch)
        assertTrue(created.created)
        assertFalse(created.checked_out)
        assertTrue(checkedOut.checked_out)
        assertFalse(checkedOut.created)
    }
}
