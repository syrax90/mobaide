package com.mobaide.app.ui.projects.git

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.ExposedDropdownMenuBox
import androidx.compose.material3.ExposedDropdownMenuDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.text.input.KeyboardCapitalization
import androidx.compose.ui.tooling.preview.Preview
import androidx.compose.ui.unit.dp
import com.mobaide.app.data.network.dto.GitItem
import com.mobaide.app.ui.common.pixel.PixelBottomSheet
import com.mobaide.app.ui.common.pixel.PixelButton
import com.mobaide.app.ui.common.pixel.PixelButtonStyle
import com.mobaide.app.ui.common.pixel.PixelCheckbox
import com.mobaide.app.ui.common.pixel.PixelTextFieldDefaults
import com.mobaide.app.ui.theme.ChromeFontFamily
import com.mobaide.app.ui.theme.MobaideTheme

const val GIT_NEW_BRANCH_SHEET_TAG = "git_new_branch_sheet"
const val GIT_NEW_BRANCH_NAME_TAG = "git_new_branch_name"
const val GIT_NEW_BRANCH_START_TAG = "git_new_branch_start"
const val GIT_NEW_BRANCH_CHECKOUT_TAG = "git_new_branch_checkout"
const val GIT_NEW_BRANCH_SUBMIT_TAG = "git_new_branch_submit"
const val GIT_NEW_BRANCH_ERROR_TAG = "git_new_branch_error"

/** One entry of the start-point picker; a null [value] is the agent's default, `HEAD`. */
internal data class StartPointOption(val value: String?, val label: String)

internal fun startPointOptions(items: List<GitItem>): List<StartPointOption> {
    val local = items.filter { it.branch != null && it.kind != GitItem.KIND_REMOTE_BRANCH }
    val remote = items.filter { it.kind == GitItem.KIND_REMOTE_BRANCH }
    return listOf(StartPointOption(null, "HEAD (current commit)")) +
        local.map { StartPointOption(it.branch, primaryLabel(it)) } +
        remote.map { StartPointOption(it.branch, "${primaryLabel(it)} (remote)") }
}

/**
 * The New branch sheet: a name, a start point defaulting to the checked-out
 * branch, and an opt-in checkout in the project folder. Field text is local,
 * as in the commit sheet; the ViewModel only sees the submit.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
internal fun GitNewBranchSheet(
    state: NewBranchSheetState,
    items: List<GitItem>,
    projectName: String,
    onDismiss: () -> Unit,
    onCreate: (name: String, startPoint: String?, checkout: Boolean) -> Unit,
) {
    val focusRequester = remember { FocusRequester() }
    PixelBottomSheet(
        onDismissRequest = { if (!state.creating) onDismiss() },
        sheetState = rememberModalBottomSheetState(
            skipPartiallyExpanded = true,
            confirmValueChange = { !state.creating },
        ),
        modifier = Modifier.testTag(GIT_NEW_BRANCH_SHEET_TAG),
    ) {
        GitNewBranchForm(
            state = state,
            items = items,
            projectName = projectName,
            focusRequester = focusRequester,
            onDismiss = onDismiss,
            onCreate = onCreate,
        )
    }
    LaunchedEffect(Unit) { focusRequester.requestFocus() }
}

/** `internal` so a test can compose the form without a sheet window. */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
internal fun GitNewBranchForm(
    state: NewBranchSheetState,
    items: List<GitItem>,
    projectName: String,
    onDismiss: () -> Unit,
    onCreate: (name: String, startPoint: String?, checkout: Boolean) -> Unit,
    modifier: Modifier = Modifier,
    focusRequester: FocusRequester = remember { FocusRequester() },
) {
    val options = remember(items) { startPointOptions(items) }
    val current = remember(items) { items.firstOrNull { it.is_current }?.branch }
    var name by rememberSaveable { mutableStateOf("") }
    var startPoint by rememberSaveable { mutableStateOf(current) }
    var checkout by rememberSaveable { mutableStateOf(false) }
    var pickerOpen by remember { mutableStateOf(false) }
    val busy = state.creating
    val submittable = !busy && name.isNotBlank()
    val selectedLabel = options.firstOrNull { it.value == startPoint }?.label ?: options.first().label

    Column(
        modifier = modifier
            .fillMaxWidth()
            .imePadding()
            .verticalScroll(rememberScrollState())
            .padding(horizontal = 16.dp)
            .padding(bottom = 24.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Text(
            text = "New branch",
            style = MaterialTheme.typography.titleMedium.copy(fontFamily = ChromeFontFamily),
            color = MaterialTheme.colorScheme.primary,
        )
        OutlinedTextField(
            colors = PixelTextFieldDefaults.colors(),
            value = name,
            onValueChange = { name = it },
            modifier = Modifier
                .fillMaxWidth()
                .focusRequester(focusRequester)
                .testTag(GIT_NEW_BRANCH_NAME_TAG),
            label = { Text("Name") },
            placeholder = { Text("feature/login") },
            singleLine = true,
            enabled = !busy,
            keyboardOptions = KeyboardOptions(
                capitalization = KeyboardCapitalization.None,
                autoCorrectEnabled = false,
            ),
        )
        ExposedDropdownMenuBox(
            expanded = pickerOpen,
            onExpandedChange = { if (!busy) pickerOpen = !pickerOpen },
        ) {
            OutlinedTextField(
                colors = PixelTextFieldDefaults.colors(),
                value = selectedLabel,
                onValueChange = { /* read-only — selection-only field */ },
                readOnly = true,
                enabled = !busy,
                label = { Text("Start point") },
                trailingIcon = { ExposedDropdownMenuDefaults.TrailingIcon(expanded = pickerOpen) },
                modifier = Modifier
                    .fillMaxWidth()
                    .menuAnchor()
                    .testTag(GIT_NEW_BRANCH_START_TAG),
            )
            ExposedDropdownMenu(
                expanded = pickerOpen,
                onDismissRequest = { pickerOpen = false },
            ) {
                options.forEach { option ->
                    DropdownMenuItem(
                        text = { Text(option.label) },
                        onClick = {
                            startPoint = option.value
                            pickerOpen = false
                        },
                    )
                }
            }
        }
        Row(verticalAlignment = Alignment.CenterVertically) {
            PixelCheckbox(
                checked = checkout,
                onCheckedChange = { checkout = it },
                enabled = !busy,
                modifier = Modifier.testTag(GIT_NEW_BRANCH_CHECKOUT_TAG),
            )
            Spacer(Modifier.width(4.dp))
            Column {
                Text(
                    "Check out in the project folder of $projectName",
                    style = MaterialTheme.typography.bodyMedium,
                )
                Text(
                    text = "Refused while a session runs in that folder.",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        }
        state.error?.let { error ->
            Text(
                text = error,
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.error,
                modifier = Modifier.testTag(GIT_NEW_BRANCH_ERROR_TAG),
            )
        }
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.spacedBy(12.dp, Alignment.End),
        ) {
            PixelButton(text = "Cancel", onClick = onDismiss, enabled = !busy)
            PixelButton(
                text = if (busy) "Creating…" else "Create",
                onClick = { onCreate(name, startPoint, checkout) },
                style = PixelButtonStyle.Primary,
                enabled = submittable,
                modifier = Modifier.testTag(GIT_NEW_BRANCH_SUBMIT_TAG),
            )
        }
    }
}

@Composable
private fun GitNewBranchFormPreviewContent() {
    GitNewBranchForm(
        state = NewBranchSheetState(),
        items = listOf(
            GitItem(kind = GitItem.KIND_BRANCH_WITH_WORKTREE, branch = "main", is_current = true),
            GitItem(kind = GitItem.KIND_REMOTE_BRANCH, branch = "origin/feature/x", remote = "origin"),
        ),
        projectName = "alpha",
        onDismiss = {},
        onCreate = { _, _, _ -> },
    )
}

@Preview(name = "New branch sheet dark", showBackground = true, backgroundColor = 0xFF121212)
@Composable
private fun GitNewBranchFormPreviewDark() {
    MobaideTheme(darkTheme = true) { GitNewBranchFormPreviewContent() }
}

@Preview(name = "New branch sheet light", showBackground = true, backgroundColor = 0xFFF4F4F4)
@Composable
private fun GitNewBranchFormPreviewLight() {
    MobaideTheme(darkTheme = false) { GitNewBranchFormPreviewContent() }
}
