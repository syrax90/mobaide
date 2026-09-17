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
 * The Android arm of the stage / discard / commit, job and rebase golden fixtures under
 * `tests/protocol_fixtures/dto/agent/`. The request shapes are what this
 * client sends, so they must also encode back to the wire; the responses
 * decode from `wire` and, where a case carries one, from `decoded_canonical`
 * (the wire minus its nulls) to the same value under the `RestApi` decoder
 * configuration.
 */
class GitMutationDtoFixtureTest {

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
        fixture("GitPathsRequest").assertCanonicalDecodesLikeWire(GitPathsRequest.serializer())
        fixture("GitDiscardRequest").assertCanonicalDecodesLikeWire(GitDiscardRequest.serializer())
        fixture("GitMutationResponse").assertCanonicalDecodesLikeWire(GitMutationResponse.serializer())
        fixture("GitCommitRequest").assertCanonicalDecodesLikeWire(GitCommitRequest.serializer())
        fixture("GitCommitResponse").assertCanonicalDecodesLikeWire(GitCommitResponse.serializer())
        fixture("GitCommitContextResponse")
            .assertCanonicalDecodesLikeWire(GitCommitContextResponse.serializer())
        fixture("GitPullRequest").assertCanonicalDecodesLikeWire(GitPullRequest.serializer())
        fixture("GitPushRequest").assertCanonicalDecodesLikeWire(GitPushRequest.serializer())
        fixture("GitJobResponse").assertCanonicalDecodesLikeWire(GitJobResponse.serializer())
        fixture("GitRebaseRequest").assertCanonicalDecodesLikeWire(GitRebaseRequest.serializer())
        fixture("GitRebaseResponse").assertCanonicalDecodesLikeWire(GitRebaseResponse.serializer())
    }

    @Test
    fun rebaseRequest_carriesTheBase_andEncodesBackToTheWire() {
        val fixture = fixture("GitRebaseRequest")

        assertEquals("main", fixture.decode(GitRebaseRequest.serializer(), "local_branch").onto)
        assertEquals("origin/main", fixture.decode(GitRebaseRequest.serializer(), "remote_tracking_branch").onto)
        for (case in fixture.cases) {
            val decoded = json.decodeFromJsonElement(GitRebaseRequest.serializer(), case.wire)
            assertEquals(case.name, case.wire, json.encodeToJsonElement(GitRebaseRequest.serializer(), decoded))
        }
    }

    @Test
    fun rebaseResponse_readsEachOutcome_theStopsProgress_andAnOpenOutcome() {
        val fixture = fixture("GitRebaseResponse")

        val stopped = fixture.decode(GitRebaseResponse.serializer(), "stopped_on_conflict")
        val bare = fixture.decode(GitRebaseResponse.serializer(), "stopped_detached_without_details")

        assertEquals(GitRebaseResponse.OUTCOME_REBASED, fixture.decode(GitRebaseResponse.serializer(), "rebased").outcome)
        assertEquals(GitRebaseResponse.OUTCOME_UP_TO_DATE, fixture.decode(GitRebaseResponse.serializer(), "up_to_date").outcome)
        assertEquals(GitRebaseResponse.OUTCOME_ABORTED, fixture.decode(GitRebaseResponse.serializer(), "aborted").outcome)
        assertEquals("future_outcome", fixture.decode(GitRebaseResponse.serializer(), "unknown_outcome").outcome)
        assertEquals(GitRebaseResponse.OUTCOME_STOPPED, stopped.outcome)
        assertEquals(
            GitRebaseInfo(
                branch = "topic",
                onto_oid = "89abcdef0123456789abcdef0123456789abcdef",
                onto_short_oid = "89abcde",
                onto_ref = "main",
                stopped_on = GitRebaseCommit(
                    "fedcba9876543210fedcba9876543210fedcba98", "fedcba9", "Fix the login redirect",
                ),
                step = 2,
                total = 5,
            ),
            stopped.rebase,
        )
        assertNull(stopped.message)
        assertEquals(GitRebaseInfo(), bare.rebase)
        assertEquals(
            "error: The following untracked working tree files would be overwritten by merge:",
            bare.message,
        )
    }

    @Test
    fun pathsRequest_listsBothSidesOfARename_andEncodesBackToTheWire() {
        val fixture = fixture("GitPathsRequest")

        val rename = fixture.decode(GitPathsRequest.serializer(), "rename_both_paths")

        assertEquals(listOf("src/new name.py", "src/old.py"), rename.paths)
        for (case in fixture.cases) {
            val decoded = json.decodeFromJsonElement(GitPathsRequest.serializer(), case.wire)
            assertEquals(case.name, case.wire, json.encodeToJsonElement(GitPathsRequest.serializer(), decoded))
        }
    }

    @Test
    fun discardRequest_carriesTheScope_andEncodesBackToTheWire() {
        val fixture = fixture("GitDiscardRequest")

        val unstaged = fixture.decode(GitDiscardRequest.serializer(), "unstaged")
        val untracked = fixture.decode(GitDiscardRequest.serializer(), "untracked")

        assertEquals(GitDiscardRequest.SCOPE_UNSTAGED, unstaged.scope)
        assertEquals(listOf("src/app.py", "docs/README.md"), unstaged.paths)
        assertEquals(GitDiscardRequest.SCOPE_UNTRACKED, untracked.scope)
        assertEquals(listOf("notes.txt"), untracked.paths)
        for (case in fixture.cases) {
            val decoded = json.decodeFromJsonElement(GitDiscardRequest.serializer(), case.wire)
            assertEquals(case.name, case.wire, json.encodeToJsonElement(GitDiscardRequest.serializer(), decoded))
        }
    }

    @Test
    fun mutationResponse_isOk() {
        assertTrue(fixture("GitMutationResponse").decode(GitMutationResponse.serializer(), "ok").ok)
    }

    @Test
    fun commitRequest_readsTheFlags_andRoundTripsWhenTheyAreSet() {
        val fixture = fixture("GitCommitRequest")

        val plain = fixture.decode(GitCommitRequest.serializer(), "subject_only")
        val amend = fixture.decode(GitCommitRequest.serializer(), "amend_no_verify")

        assertEquals("Fix the login redirect", plain.message)
        assertFalse(plain.amend)
        assertFalse(plain.no_verify)
        assertEquals(
            "Fix the login redirect\n\nThe callback URL was built before the port was known.",
            amend.message,
        )
        assertTrue(amend.amend)
        assertTrue(amend.no_verify)
        assertEquals(
            fixture.cases.first { it.name == "amend_no_verify" }.wire,
            json.encodeToJsonElement(GitCommitRequest.serializer(), amend),
        )
        // The encoder drops defaults, which the agent's model supplies: a
        // plain request travels as `{"message": …}`.
        assertEquals(
            setOf("message"),
            (json.encodeToJsonElement(GitCommitRequest.serializer(), plain) as JsonObject).keys,
        )
    }

    @Test
    fun commitResponse_namesTheNewHead() {
        val committed = fixture("GitCommitResponse").decode(GitCommitResponse.serializer(), "committed")

        assertEquals("4890423520f4dcd158b85374f44fc8819a813ac2", committed.oid)
        assertEquals("4890423", committed.short_oid)
        assertEquals("Fix the login redirect", committed.subject)
    }

    @Test
    fun commitContext_readsTheAuthorAndHead_orTheirAbsence() {
        val fixture = fixture("GitCommitContextResponse")

        val ready = fixture.decode(GitCommitContextResponse.serializer(), "ready")
        val unborn = fixture.decode(GitCommitContextResponse.serializer(), "unborn_no_identity")

        assertEquals(GitCommitAuthor("Ada Lovelace", "ada@example.com"), ready.author)
        assertEquals("4890423", ready.head?.short_oid)
        assertEquals("Fix the login redirect", ready.head?.subject)
        assertEquals("The callback URL was built before the port was known.", ready.head?.body)
        assertNull(unborn.author)
        assertNull(unborn.head)
    }
}
