package com.mobaide.app.ui.terminal

import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.padding
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.MoreVert
import androidx.compose.material.icons.outlined.AccountTree
import androidx.compose.material.icons.outlined.Commit
import androidx.compose.material.icons.outlined.Difference
import androidx.compose.material.icons.outlined.Sync
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.tooling.preview.Preview
import androidx.compose.ui.unit.dp
import com.mobaide.app.ui.common.pixel.PixelIconButton
import com.mobaide.app.ui.theme.MobaideTheme
import com.mobaide.app.ui.theme.chromeCaps

const val TERMINAL_MENU_GIT_CHANGES_TAG = "terminal_menu_git_changes"
const val TERMINAL_MENU_GIT_COMMIT_TAG = "terminal_menu_git_commit"
const val TERMINAL_MENU_GIT_SYNC_TAG = "terminal_menu_git_sync"
const val TERMINAL_MENU_GIT_BRANCHES_TAG = "terminal_menu_git_branches"

/**
 * The terminal top bar's `⋯` menu. `internal` (vs the screen file's usual
 * `private`) so a test can compose it directly.
 *
 * The "Git" heading is a hand-rolled, non-clickable label: material3 1.3
 * (Compose BOM 2024.12) predates `MenuDefaults.DropdownMenuGroupLabel`, and
 * this is shaped for a mechanical swap once the BOM moves. Commit… exists only
 * on an agent that advertises `capabilities.git.commit`, Fetch / Pull / Push…
 * on any of the three job keys, Branches… (a link to the project's Git
 * screen) on `capabilities.git.branches` for a project-backed session;
 * Rebase… is the one reserved slot left under the heading, after Branches….
 */
@Composable
internal fun TerminalOverflowMenu(
    onOpenGitChanges: () -> Unit,
    onOpenGitCommit: () -> Unit = {},
    commitAvailable: Boolean = false,
    onOpenGitSync: () -> Unit = {},
    syncAvailable: Boolean = false,
    onOpenGitBranches: () -> Unit = {},
    branchesAvailable: Boolean = false,
) {
    var menuExpanded by remember { mutableStateOf(false) }
    Box {
        PixelIconButton(Icons.Default.MoreVert, "More options", onClick = { menuExpanded = true })
        DropdownMenu(expanded = menuExpanded, onDismissRequest = { menuExpanded = false }) {
            MenuSectionLabel("Git")
            DropdownMenuItem(
                text = { Text("Changes…") },
                onClick = {
                    menuExpanded = false
                    onOpenGitChanges()
                },
                leadingIcon = { Icon(Icons.Outlined.Difference, contentDescription = null) },
                modifier = Modifier.testTag(TERMINAL_MENU_GIT_CHANGES_TAG),
            )
            if (commitAvailable) {
                DropdownMenuItem(
                    text = { Text("Commit…") },
                    onClick = {
                        menuExpanded = false
                        onOpenGitCommit()
                    },
                    leadingIcon = { Icon(Icons.Outlined.Commit, contentDescription = null) },
                    modifier = Modifier.testTag(TERMINAL_MENU_GIT_COMMIT_TAG),
                )
            }
            if (syncAvailable) {
                DropdownMenuItem(
                    text = { Text("Fetch / Pull / Push…") },
                    onClick = {
                        menuExpanded = false
                        onOpenGitSync()
                    },
                    leadingIcon = { Icon(Icons.Outlined.Sync, contentDescription = null) },
                    modifier = Modifier.testTag(TERMINAL_MENU_GIT_SYNC_TAG),
                )
            }
            if (branchesAvailable) {
                DropdownMenuItem(
                    text = { Text("Branches…") },
                    onClick = {
                        menuExpanded = false
                        onOpenGitBranches()
                    },
                    leadingIcon = { Icon(Icons.Outlined.AccountTree, contentDescription = null) },
                    modifier = Modifier.testTag(TERMINAL_MENU_GIT_BRANCHES_TAG),
                )
            }
        }
    }
}

@Composable
private fun MenuSectionLabel(text: String) {
    Text(
        text = text.chromeCaps(),
        style = MaterialTheme.typography.labelSmall,
        color = MaterialTheme.colorScheme.onSurfaceVariant,
        modifier = Modifier.padding(horizontal = 12.dp, vertical = 8.dp),
    )
}

@Preview(name = "Overflow menu dark", showBackground = true, backgroundColor = 0xFF121212)
@Composable
private fun TerminalOverflowMenuPreviewDark() {
    MobaideTheme(darkTheme = true) {
        TerminalOverflowMenu(
            onOpenGitChanges = {},
            commitAvailable = true,
            syncAvailable = true,
            branchesAvailable = true,
        )
    }
}

@Preview(name = "Overflow menu light", showBackground = true, backgroundColor = 0xFFF4F4F4)
@Composable
private fun TerminalOverflowMenuPreviewLight() {
    MobaideTheme(darkTheme = false) { TerminalOverflowMenu(onOpenGitChanges = {}) }
}
