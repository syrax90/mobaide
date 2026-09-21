package com.mobaide.app.ui.terminal.git

import com.mobaide.app.data.network.dto.GitConflictResponse
import com.mobaide.app.data.network.dto.GitConflictSide
import com.mobaide.app.data.network.dto.GitRepoInfo
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The pure half of the conflict resolver: the marker scan (which must agree
 * with the agent's bytes-mode regex), the jump to the next conflict, the
 * sides' names and roles, the whole-file picks and the copy shared with the
 * web and iOS clients.
 */
class GitConflictUiStateTest {

    private fun conflict(
        path: String = "src/login.py",
        code: String = "UU",
        state: String = GitRepoInfo.STATE_REBASING,
        kind: String = GitConflictResponse.KIND_TEXT,
        content: String? = "a\n<<<<<<< HEAD\nmain\n=======\ntopic\n>>>>>>> fedcba9\n",
        ours: GitConflictSide = GitConflictSide("origin/main", true),
        theirs: GitConflictSide = GitConflictSide("fedcba9 Fix the login redirect", true),
    ) = GitConflictResponse(
        path = path, code = code, state = state, kind = kind, content = content,
        token = "t1", markers = 1, ours = ours, theirs = theirs,
    )

    private val twoBlocks =
        "top\n<<<<<<< a\nx\n=======\ny\n>>>>>>> b\nmid\n<<<<<<< a\nz\n=======\n>>>>>>> b\n"

    @Test
    fun scan_countsOneConflictPerStartMarker() {
        assertEquals(ConflictScan(conflicts = 2, hasMarkers = true, hasBase = false), scanConflictText(twoBlocks))
    }

    @Test
    fun scan_seesTheOriginalLinesOfADiff3Block() {
        assertEquals(
            ConflictScan(conflicts = 1, hasMarkers = true, hasBase = true),
            scanConflictText("<<<<<<< ours\nm\n||||||| base\no\n=======\nt\n>>>>>>> theirs\n"),
        )
    }

    @Test
    fun scan_findsNothingInResolvedText_aBareSeparatorIncluded() {
        assertEquals(ConflictScan(), scanConflictText("title\n=======\nbody\n"))
        assertEquals(ConflictScan(), scanConflictText(""))
    }

    @Test
    fun scan_reportsALeftoverEndMarker_asNoOpenConflict() {
        assertEquals(
            ConflictScan(conflicts = 0, hasMarkers = true, hasBase = false),
            scanConflictText("kept\n>>>>>>> fedcba9 (Fix)\n"),
        )
    }

    @Test
    fun scan_matchesTheAgentsMarkerRule() {
        val cases = listOf(
            "<<<<<<<" to true,
            "<<<<<<<\r\nx" to true,
            "<<<<<<<\tlabel" to true,
            "<<<<<<<\u000Cx" to true,
            "<<<<<<<\u000Bx" to true,
            "<<<<<<<<< label" to true,
            "<<<<<< label" to false,
            "<<<<<<<label" to false,
            " <<<<<<< label" to false,
            "x <<<<<<< label" to false,
            // whitespace to Kotlin, not to Python's bytes-mode \s
            "<<<<<<< label" to false,
            "<<<<<<< label" to false,
        )

        for ((text, marker) in cases) {
            val scan = scanConflictText(text)
            assertEquals(text, marker, scan.hasMarkers)
            assertEquals(text, if (marker) 1 else 0, scan.conflicts)
        }
    }

    @Test
    fun exceedsByteLimit_letsExactlyTheLimitThrough_andStopsTheByteAfterIt() {
        assertFalse(exceedsByteLimit("a".repeat(10), 10))
        assertTrue(exceedsByteLimit("a".repeat(11), 10))
        assertFalse(exceedsByteLimit("", 0))
    }

    @Test
    fun exceedsByteLimit_countsUtf8Bytes_notTheUtf16UnitsOfTheTextField() {
        // `é` is one unit and two bytes; an emoji two units and four bytes.
        assertFalse(exceedsByteLimit("é".repeat(5), 10))
        assertTrue(exceedsByteLimit("é".repeat(6), 10))
        assertFalse(exceedsByteLimit("😀😀", 8))
        assertTrue(exceedsByteLimit("😀😀😀", 8))
    }

    @Test
    fun scan_flagsATextOverTheByteLimit_andNeverWithoutOne() {
        val resolved = "é".repeat(600)

        assertEquals(ConflictScan(tooLarge = true), scanConflictText(resolved, byteLimit = 1024))
        assertEquals(ConflictScan(), scanConflictText(resolved, byteLimit = 1200))
        assertEquals(ConflictScan(), scanConflictText(resolved))
        assertEquals(
            ConflictScan(conflicts = 2, hasMarkers = true, hasBase = false, tooLarge = true),
            scanConflictText(twoBlocks, byteLimit = 16),
        )
    }

    @Test
    fun tooLargeToSaveNotice_statesTheAgentsLimitInKb() {
        assertEquals("Too large to save here (over 256 KB).", tooLargeToSaveNotice(262144))
    }

    @Test
    fun nextConflictRange_findsTheMarkerLineAfterTheCaret_andWraps() {
        val first = twoBlocks.indexOf("<<<<<<<")
        val second = twoBlocks.indexOf("<<<<<<<", first + 1)
        val length = "<<<<<<< a".length

        assertEquals(first until first + length, nextConflictRange(twoBlocks, 0))
        assertEquals(second until second + length, nextConflictRange(twoBlocks, first + 3))
        // a caret parked at the start of a marker line moves on
        assertEquals(second until second + length, nextConflictRange(twoBlocks, first))
        assertEquals(first until first + length, nextConflictRange(twoBlocks, second))
        assertEquals(first until first + length, nextConflictRange(twoBlocks, twoBlocks.length))
    }

    @Test
    fun nextConflictRange_isNullWhenNoConflictIsLeft() {
        assertNull(nextConflictRange("kept\n>>>>>>> b\n", 0))
        assertNull(nextConflictRange("", 0))
    }

    @Test
    fun nextConflictRange_countsUtf16Units_likeTheTextFieldsSelection() {
        val text = "😀 ж\n<<<<<<< a\n"

        assertEquals(5 until 14, nextConflictRange(text, 0))
    }

    @Test
    fun conflictsLeftLabel_countsTheConflicts_andTellsAStrayMarkerFromACleanFile() {
        assertEquals("1 conflict left", conflictsLeftLabel(1, true))
        assertEquals("3 conflicts left", conflictsLeftLabel(3, true))
        assertEquals("A conflict marker is left", conflictsLeftLabel(0, true))
        assertEquals("No conflict markers left", conflictsLeftLabel(0, false))
    }

    @Test
    fun sideName_usesTheAgentsLabel_andFallsBackToNeutralNames() {
        val unlabelled = conflict(ours = GitConflictSide(null, true), theirs = GitConflictSide("", true))

        assertEquals("origin/main", sideName(conflict(), GitConflictResponse.SIDE_OURS))
        assertEquals("fedcba9 Fix the login redirect", sideName(conflict(), GitConflictResponse.SIDE_THEIRS))
        assertEquals("Current branch", sideName(unlabelled, GitConflictResponse.SIDE_OURS))
        assertEquals("Incoming change", sideName(unlabelled, GitConflictResponse.SIDE_THEIRS))
    }

    @Test
    fun sideRole_explainsARebaseTheRightWayRound_aMerge_andAnythingElse() {
        assertEquals(
            "the branch you are rebasing onto, plus your commits already replayed",
            sideRole(GitRepoInfo.STATE_REBASING, GitConflictResponse.SIDE_OURS),
        )
        assertEquals("your commit being replayed", sideRole(GitRepoInfo.STATE_REBASING, GitConflictResponse.SIDE_THEIRS))
        assertEquals("the branch you are on", sideRole(GitRepoInfo.STATE_MERGING, GitConflictResponse.SIDE_OURS))
        assertEquals("the branch being merged in", sideRole(GitRepoInfo.STATE_MERGING, GitConflictResponse.SIDE_THEIRS))
        assertEquals("what you have checked out", sideRole("cherry_picking", GitConflictResponse.SIDE_OURS))
        assertEquals("the change being applied", sideRole(GitRepoInfo.STATE_CLEAN, GitConflictResponse.SIDE_THEIRS))
    }

    @Test
    fun conflictCaption_readsEveryCodeInPlainWords() {
        val captions = mapOf(
            "UU" to "Both sides changed this file.",
            "AA" to "Both sides added this file.",
            "DU" to "origin/main deleted this file; fedcba9 Fix the login redirect changed it.",
            "UD" to "origin/main changed this file; fedcba9 Fix the login redirect deleted it.",
            "DD" to "Both sides deleted this file.",
            "AU" to "Only origin/main has this file.",
            "UA" to "Only fedcba9 Fix the login redirect has this file.",
            "ZZ" to "The two sides disagree about this file.",
        )

        for ((code, caption) in captions) {
            assertEquals(code, caption, conflictCaption(conflict(code = code)))
        }
    }

    @Test
    fun conflictLegend_mapsTheRegionsToTheLabelledSides_withTheBaseWhenPresent() {
        assertEquals(
            listOf(
                "Between <<<<<<< and =======: origin/main — the branch you are rebasing onto, " +
                    "plus your commits already replayed.",
                "Between ======= and >>>>>>>: fedcba9 Fix the login redirect — your commit being replayed.",
            ),
            conflictLegend(conflict(), hasBase = false),
        )
        assertEquals(
            listOf(
                "Between <<<<<<< and |||||||: origin/main — the branch you are on.",
                "Between ||||||| and =======: the original lines, before either change.",
                "Between ======= and >>>>>>>: fedcba9 Fix the login redirect — the branch being merged in.",
            ),
            conflictLegend(conflict(state = GitRepoInfo.STATE_MERGING), hasBase = true),
        )
    }

    @Test
    fun onlyTextThatArrivedWithItsContent_isEditable() {
        assertTrue(isEditableConflict(conflict()))
        assertTrue(isEditableConflict(conflict(content = "")))
        assertFalse(isEditableConflict(conflict(content = null)))
        assertNull(conflictKindNotice(conflict()))
    }

    @Test
    fun conflictKindNotice_explainsEveryKindThatCannotBeEdited_unknownOnesIncluded() {
        val notices = mapOf(
            "too_large" to "This file is too large to edit here (over 256 KB). " +
                "Pick a side, or resolve it in the terminal.",
            "binary" to "This is a binary file — pick a side.",
            "encoding" to "This file is not UTF-8 text — pick a side, or resolve it in the terminal.",
            "missing" to "The file is not in the working tree — pick a side.",
            "symlink" to "This is a symbolic link — pick a side.",
            "submodule" to "Submodule conflicts are resolved in the terminal.",
            "unsupported" to "This conflict can't be resolved here — use the terminal.",
            "hologram" to "This conflict can't be resolved here — use the terminal.",
        )

        for ((kind, notice) in notices) {
            val shown = conflict(kind = kind, content = null)
            assertFalse(kind, isEditableConflict(shown))
            assertEquals(kind, notice, conflictKindNotice(shown))
        }
    }

    @Test
    fun wholeFilePicks_existForEveryKnownShape_butASubmoduleOrADirectoryInTheWay() {
        for (kind in listOf("text", "too_large", "binary", "encoding", "missing", "symlink")) {
            assertTrue(kind, canTakeSide(conflict(kind = kind)))
        }
        for (kind in listOf("submodule", "unsupported", "hologram")) {
            val shown = conflict(kind = kind, content = null)
            assertFalse(kind, canTakeSide(shown))
            assertEquals(kind, emptyList<TakeChoice>(), takeChoices(shown))
        }
    }

    @Test
    fun startOver_needsEditableTextBothSidesStillHave() {
        assertTrue(canStartOver(conflict()))
        assertFalse(canStartOver(conflict(code = "DU", ours = GitConflictSide("origin/main", false))))
        assertFalse(canStartOver(conflict(code = "UD", theirs = GitConflictSide("x", false))))
        assertFalse(canStartOver(conflict(kind = "binary", content = null)))
    }

    @Test
    fun takeChoices_offerEitherSideByItsLabel_whenBothHaveTheFile() {
        val body = "Every conflict in login.py is settled with this side and the file is marked " +
            "resolved. Edits made to it are discarded."

        assertEquals(
            listOf(
                TakeChoice(
                    side = "ours",
                    label = "Use origin/main for the whole file",
                    title = "Use origin/main for the whole file?",
                    body = body,
                    confirm = "Use this side",
                    danger = false,
                ),
                TakeChoice(
                    side = "theirs",
                    label = "Use fedcba9 Fix the login redirect for the whole file",
                    title = "Use fedcba9 Fix the login redirect for the whole file?",
                    body = body,
                    confirm = "Use this side",
                    danger = false,
                ),
            ),
            takeChoices(conflict()),
        )
    }

    @Test
    fun takeChoices_turnTheSideThatDeletedTheFile_intoTheDestructivePick() {
        val deletedByUs = conflict(
            path = "docs/old.md", code = "DU", ours = GitConflictSide("origin/main", false),
        )

        assertEquals(
            listOf(
                TakeChoice(
                    side = "ours",
                    label = "Delete the file (origin/main)",
                    title = "Delete old.md?",
                    body = "The file is removed, as on origin/main, and the conflict is marked resolved.",
                    confirm = "Delete file",
                    danger = true,
                ),
                TakeChoice(
                    side = "theirs",
                    label = "Keep the file (fedcba9 Fix the login redirect)",
                    title = "Keep old.md?",
                    body = "The file stays as fedcba9 Fix the login redirect has it and is marked resolved.",
                    confirm = "Keep file",
                    danger = false,
                ),
            ),
            takeChoices(deletedByUs),
        )
    }

    @Test
    fun takeChoices_mirrorWhenTheOtherSideDeletedIt_andLeaveOneWayOutWhenNeitherHasIt() {
        val deletedByThem = conflict(code = "UD", theirs = GitConflictSide(null, false))
        val bothDeleted = conflict(
            code = "DD", kind = "missing", content = null,
            ours = GitConflictSide("origin/main", false), theirs = GitConflictSide("x", false),
        )

        assertEquals(
            listOf(
                Triple("ours", "Keep the file (origin/main)", false),
                Triple("theirs", "Delete the file (Incoming change)", true),
            ),
            takeChoices(deletedByThem).map { Triple(it.side, it.label, it.danger) },
        )
        assertEquals(
            listOf(Triple("ours", "Delete the file", true)),
            takeChoices(bothDeleted).map { Triple(it.side, it.label, it.danger) },
        )
    }

    @Test
    fun prompts_nameTheFile_notItsDirectory() {
        assertEquals(
            ConflictPrompt(
                title = "Start over?",
                body = "login.py is rebuilt from both sides. Every edit made to it since the " +
                    "conflict — here or in the terminal — is discarded.",
            ),
            startOverPrompt("src/login.py"),
        )
        assertEquals(
            ConflictPrompt(
                title = "Restore the conflict?",
                body = "login.py goes back to its conflicted state. The resolution you applied is discarded.",
                confirm = "Restore conflict",
            ),
            restorePrompt("src/login.py"),
        )
        assertEquals(
            ConflictPrompt(title = "Discard your edits?", body = "Your changes to login.py have not been saved."),
            discardEditsPrompt("src/login.py"),
        )
        assertEquals("Start over", START_OVER_LABEL)
        assertEquals("Start over with original lines", START_OVER_WITH_BASE_LABEL)
    }

    @Test
    fun notices_reportEachOutcome() {
        assertEquals("Resolved src/login.py.", resolvedNotice("src/login.py"))
        assertEquals("Saved src/login.py — the conflict is still open.", draftSavedNotice("src/login.py"))
        assertEquals("Restored the conflict in src/login.py.", restoredNotice("src/login.py"))
        assertEquals(
            "login.py changed on the agent machine since you opened it.",
            fileChangedNotice("src/login.py"),
        )
        assertEquals("Applying will overwrite the other change.", KEEP_EDITS_WARNING)
        assertEquals("login.py is no longer in conflict.", resolvedElsewhereNotice("src/login.py"))
    }

    @Test
    fun ready_holdsApplyWhileMarkersRemain_andTheDraftUntilTheTextChanged() {
        val clean = GitConflictUiState.Ready(conflict(), ConflictScan(), dirty = true)

        assertTrue(clean.canApply)
        assertTrue(clean.canSaveDraft)
        assertFalse(clean.copy(scan = ConflictScan(conflicts = 0, hasMarkers = true)).canApply)
        assertTrue(clean.copy(dirty = false).canApply)
        assertFalse(clean.copy(dirty = false).canSaveDraft)
    }

    @Test
    fun ready_offersNoSave_whileBusy_onceGone_orForAFileThatIsNotText() {
        val clean = GitConflictUiState.Ready(conflict(), ConflictScan(), dirty = true)
        val binary = clean.copy(conflict = conflict(kind = "binary", content = null))

        for (blocked in listOf(clean.copy(busy = true), clean.copy(gone = true), binary)) {
            assertFalse(blocked.canApply)
            assertFalse(blocked.canSaveDraft)
        }
        // stale alone blocks nothing: the agent refuses again until the user picks
        assertTrue(clean.copy(stale = true).canApply)
    }

    @Test
    fun ready_offersNeitherSave_forATextTooLargeToSend() {
        val oversized = GitConflictUiState.Ready(conflict(), ConflictScan(tooLarge = true), dirty = true)

        assertFalse(oversized.canApply)
        assertFalse(oversized.canSaveDraft)
        assertTrue(oversized.copy(scan = ConflictScan()).canApply)
        assertTrue(oversized.copy(scan = ConflictScan()).canSaveDraft)
    }
}
