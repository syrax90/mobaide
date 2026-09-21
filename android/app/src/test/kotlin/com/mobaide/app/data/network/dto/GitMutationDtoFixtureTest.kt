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
 * The Android arm of the stage / discard / commit, job, rebase and conflict golden fixtures under
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
        fixture("GitConflictResponse").assertCanonicalDecodesLikeWire(GitConflictResponse.serializer())
        fixture("GitConflictResolveRequest")
            .assertCanonicalDecodesLikeWire(GitConflictResolveRequest.serializer())
        fixture("GitConflictTakeRequest").assertCanonicalDecodesLikeWire(GitConflictTakeRequest.serializer())
        fixture("GitConflictRestoreRequest")
            .assertCanonicalDecodesLikeWire(GitConflictRestoreRequest.serializer())
    }

    @Test
    fun conflictResponse_readsTheTextTheSidesAndTheToken() {
        val fixture = fixture("GitConflictResponse")

        val text = fixture.decode(GitConflictResponse.serializer(), "rebase_text_two_sides")
        val diff3 = fixture.decode(GitConflictResponse.serializer(), "diff3_base_sections_non_ascii")

        assertEquals("src/login.py", text.path)
        assertEquals("UU", text.code)
        assertEquals(GitRepoInfo.STATE_REBASING, text.state)
        assertEquals(GitConflictResponse.KIND_TEXT, text.kind)
        assertEquals(
            "def login():\n<<<<<<< HEAD\n    return redirect(home)\n=======\n" +
                "    return redirect(next_url)\n>>>>>>> fedcba9 (Fix the login redirect)\n",
            text.content,
        )
        assertEquals("9f2c1d4e5a6b7c8d9e0f1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d7e8f9a0b1c2d", text.token)
        assertEquals(1, text.markers)
        assertFalse(text.has_base)
        assertEquals(GitConflictSide("origin/main", true), text.ours)
        assertEquals(GitConflictSide("fedcba9 Fix the login redirect", true), text.theirs)
        assertEquals(262144, text.byte_limit)
        assertTrue(diff3.has_base)
        assertEquals(
            "a\n<<<<<<< ours\nmain\n||||||| base\norig\n=======\ntopic – ж\n>>>>>>> theirs\n",
            diff3.content,
        )
    }

    @Test
    fun conflictResponse_readsAnAbsentSide_missingLabels_andContentlessKinds() {
        val fixture = fixture("GitConflictResponse")

        val deletedByUs = fixture.decode(GitConflictResponse.serializer(), "deleted_by_us_no_markers")
        val merging = fixture.decode(GitConflictResponse.serializer(), "merging_without_labels")
        val bothDeleted = fixture.decode(GitConflictResponse.serializer(), "both_deleted_missing")

        assertEquals("DU", deletedByUs.code)
        assertFalse(deletedByUs.ours.present)
        assertTrue(deletedByUs.theirs.present)
        assertEquals("kept by the commit\n", deletedByUs.content)
        assertEquals(GitRepoInfo.STATE_MERGING, merging.state)
        assertEquals(GitConflictSide(null, true), merging.ours)
        assertEquals(GitConflictSide(null, true), merging.theirs)
        assertEquals(GitConflictResponse.KIND_MISSING, bothDeleted.kind)
        assertFalse(bothDeleted.ours.present)
        assertFalse(bothDeleted.theirs.present)
        for ((name, kind) in listOf(
            "binary" to GitConflictResponse.KIND_BINARY,
            "too_large" to GitConflictResponse.KIND_TOO_LARGE,
            "submodule" to GitConflictResponse.KIND_SUBMODULE,
            "unknown_kind" to "hologram",
        )) {
            val decoded = fixture.decode(GitConflictResponse.serializer(), name)
            assertEquals(name, kind, decoded.kind)
            assertNull(name, decoded.content)
            assertNull(name, decoded.token)
        }
    }

    @Test
    fun conflictResolveRequest_roundTripsADraft_andLeavesTheApplyDefaultToTheAgent() {
        val fixture = fixture("GitConflictResolveRequest")

        val apply = fixture.decode(GitConflictResolveRequest.serializer(), "apply_and_mark_resolved")
        val draft = fixture.decode(GitConflictResolveRequest.serializer(), "save_draft_with_markers")

        assertTrue(apply.mark_resolved)
        assertEquals("def login():\n    return redirect(next_url)\n", apply.content)
        assertEquals("9f2c1d4e5a6b7c8d9e0f1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d7e8f9a0b1c2d", apply.token)
        assertFalse(draft.mark_resolved)
        assertEquals("docs/notes with space.md", draft.path)
        assertEquals(
            fixture.cases.first { it.name == "save_draft_with_markers" }.wire,
            json.encodeToJsonElement(GitConflictResolveRequest.serializer(), draft),
        )
        // The encoder drops defaults, which the agent's model supplies: an
        // apply travels without `mark_resolved`, a draft must carry its `false`.
        assertEquals(
            setOf("path", "token", "content"),
            (json.encodeToJsonElement(GitConflictResolveRequest.serializer(), apply) as JsonObject).keys,
        )
    }

    @Test
    fun conflictTakeRequest_carriesTheSide_andEncodesBackToTheWire() {
        val fixture = fixture("GitConflictTakeRequest")

        assertEquals(
            GitConflictResponse.SIDE_OURS,
            fixture.decode(GitConflictTakeRequest.serializer(), "take_ours").side,
        )
        assertEquals(
            GitConflictTakeRequest("docs/old.md", GitConflictResponse.SIDE_THEIRS),
            fixture.decode(GitConflictTakeRequest.serializer(), "take_theirs"),
        )
        for (case in fixture.cases) {
            val decoded = json.decodeFromJsonElement(GitConflictTakeRequest.serializer(), case.wire)
            assertEquals(
                case.name, case.wire, json.encodeToJsonElement(GitConflictTakeRequest.serializer(), decoded),
            )
        }
    }

    @Test
    fun conflictRestoreRequest_readsTheStyleAndToken_andEncodesOnlyWhatDiffersFromTheDefaults() {
        val fixture = fixture("GitConflictRestoreRequest")

        val resolved = fixture.decode(GitConflictRestoreRequest.serializer(), "restore_resolved_path")
        val startOver =
            fixture.decode(GitConflictRestoreRequest.serializer(), "start_over_with_original_lines")

        assertEquals(GitConflictRestoreRequest("src/session.py"), resolved)
        assertEquals(GitConflictRestoreRequest.STYLE_MERGE, resolved.style)
        assertNull(resolved.token)
        assertEquals(GitConflictRestoreRequest.STYLE_DIFF3, startOver.style)
        assertEquals(
            fixture.cases.first { it.name == "start_over_with_original_lines" }.decoded_canonical,
            json.encodeToJsonElement(GitConflictRestoreRequest.serializer(), startOver),
        )
        assertEquals(
            setOf("path"),
            (json.encodeToJsonElement(GitConflictRestoreRequest.serializer(), resolved) as JsonObject).keys,
        )
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
