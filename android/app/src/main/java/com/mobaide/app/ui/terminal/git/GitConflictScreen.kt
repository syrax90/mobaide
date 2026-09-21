package com.mobaide.app.ui.terminal.git

import androidx.activity.compose.BackHandler
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.consumeWindowInsets
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.text.input.TextFieldLineLimits
import androidx.compose.foundation.text.input.TextFieldState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.MoreVert
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
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
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.input.KeyboardCapitalization
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.tooling.preview.Preview
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.mobaide.app.data.network.dto.GitConflictResponse
import com.mobaide.app.data.network.dto.GitConflictSide
import com.mobaide.app.data.network.dto.GitRepoInfo
import com.mobaide.app.data.network.dto.gitPathFileName
import com.mobaide.app.ui.common.pixel.PixelAlertDialog
import com.mobaide.app.ui.common.pixel.PixelButton
import com.mobaide.app.ui.common.pixel.PixelButtonStyle
import com.mobaide.app.ui.common.pixel.PixelScaffold
import com.mobaide.app.ui.common.pixel.PixelSnackbarHost
import com.mobaide.app.ui.common.pixel.PixelTextButton
import com.mobaide.app.ui.common.pixel.PixelTopBar
import com.mobaide.app.ui.theme.ChromeFontFamily
import com.mobaide.app.ui.theme.MobaideTheme
import com.mobaide.app.ui.theme.pixel
import kotlinx.coroutines.launch

const val GIT_CONFLICT_SCREEN_TAG = "git_conflict_screen"
const val GIT_CONFLICT_LOADING_TAG = "git_conflict_loading"
const val GIT_CONFLICT_ERROR_TAG = "git_conflict_error"
const val GIT_CONFLICT_OUTDATED_TAG = "git_conflict_outdated"
const val GIT_CONFLICT_SESSION_GONE_TAG = "git_conflict_session_gone"
const val GIT_CONFLICT_EDITOR_TAG = "git_conflict_editor"
const val GIT_CONFLICT_COUNT_TAG = "git_conflict_count"
const val GIT_CONFLICT_NEXT_TAG = "git_conflict_next"
const val GIT_CONFLICT_LEGEND_TOGGLE_TAG = "git_conflict_legend_toggle"
const val GIT_CONFLICT_LEGEND_TAG = "git_conflict_legend"
const val GIT_CONFLICT_APPLY_TAG = "git_conflict_apply"
const val GIT_CONFLICT_DRAFT_TAG = "git_conflict_draft"
const val GIT_CONFLICT_MENU_TAG = "git_conflict_menu"
const val GIT_CONFLICT_START_OVER_TAG = "git_conflict_start_over"
const val GIT_CONFLICT_VIEW_DIFF_TAG = "git_conflict_view_diff"
const val GIT_CONFLICT_STALE_TAG = "git_conflict_stale"
const val GIT_CONFLICT_RELOAD_TAG = "git_conflict_reload"
const val GIT_CONFLICT_KEEP_EDITS_TAG = "git_conflict_keep_edits"
const val GIT_CONFLICT_GONE_TAG = "git_conflict_gone"
const val GIT_CONFLICT_BLOCKED_TAG = "git_conflict_blocked"
const val GIT_CONFLICT_TOO_LARGE_TAG = "git_conflict_too_large"

fun gitConflictTakeTag(side: String) = "git_conflict_take_$side"

private sealed interface ConflictDialog {
    data class Take(val choice: TakeChoice) : ConflictDialog
    data object StartOver : ConflictDialog
    data object DiscardEdits : ConflictDialog
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun GitConflictScreen(
    viewModel: GitConflictViewModel,
    onBack: () -> Unit,
    onClosed: (message: String?) -> Unit,
    onViewDiff: () -> Unit,
) {
    val state by viewModel.uiState.collectAsStateWithLifecycle()
    val snackbarHostState = remember { SnackbarHostState() }
    var dialog by remember { mutableStateOf<ConflictDialog?>(null) }
    var pendingLeave by remember { mutableStateOf<(() -> Unit)?>(null) }
    val ready = state as? GitConflictUiState.Ready
    val dirty = ready?.dirty == true

    DisposableEffect(viewModel) {
        viewModel.holdWatch()
        onDispose { viewModel.releaseWatch() }
    }

    LaunchedEffect(viewModel) {
        viewModel.events.collect { event ->
            when (event) {
                is GitConflictEvent.Close -> onClosed(event.message)
                is GitConflictEvent.ShowMessage -> launch { snackbarHostState.showSnackbar(event.text) }
            }
        }
    }

    // Every way out of a dirty editor — the system back, the arrow — asks
    // first: the draft exists nowhere else. View diff is not a way out: it
    // pushes on top, and the ViewModel keeps the draft underneath.
    val leave: (() -> Unit) -> Unit = { action ->
        if (dirty) {
            pendingLeave = action
            dialog = ConflictDialog.DiscardEdits
        } else {
            action()
        }
    }
    BackHandler(enabled = dirty) { leave(onBack) }

    PixelScaffold(
        snackbarHost = { PixelSnackbarHost(snackbarHostState) },
        topBar = {
            PixelTopBar(
                title = {
                    Text(
                        text = gitPathFileName(viewModel.path),
                        style = MaterialTheme.typography.titleMedium.copy(fontFamily = ChromeFontFamily),
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                    )
                },
                navigationIcon = {
                    IconButton(onClick = { leave(onBack) }) {
                        Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = "Back")
                    }
                },
                actions = {
                    if (ready != null) {
                        ConflictMenu(
                            conflict = ready.conflict,
                            enabled = !ready.busy && !ready.gone,
                            onTake = { dialog = ConflictDialog.Take(it) },
                            onStartOver = { dialog = ConflictDialog.StartOver },
                            onViewDiff = onViewDiff,
                        )
                    }
                },
            )
        },
    ) { padding ->
        GitConflictContent(
            state = state,
            path = viewModel.path,
            editor = viewModel.editor,
            onRetry = viewModel::retry,
            onApply = viewModel::apply,
            onSaveDraft = viewModel::saveDraft,
            onNextConflict = viewModel::jumpToNextConflict,
            onReloadFile = viewModel::reloadFile,
            onKeepMyEdits = viewModel::keepMyEdits,
            onTake = { dialog = ConflictDialog.Take(it) },
            onClose = onBack,
            // The action bar rides above the keyboard, so the IME inset is
            // consumed here rather than left to a resize the edge-to-edge
            // window never performs.
            modifier = Modifier
                .fillMaxSize()
                .padding(padding)
                .consumeWindowInsets(padding)
                .imePadding()
                .testTag(GIT_CONFLICT_SCREEN_TAG),
        )
    }

    when (val open = dialog) {
        is ConflictDialog.Take -> ConflictConfirmDialog(
            title = open.choice.title,
            body = open.choice.body,
            confirm = open.choice.confirm,
            danger = open.choice.danger,
            onConfirm = {
                dialog = null
                viewModel.take(open.choice)
            },
            onDismiss = { dialog = null },
        )
        ConflictDialog.StartOver -> StartOverDialog(
            prompt = startOverPrompt(viewModel.path),
            onStartOver = { withOriginalLines ->
                dialog = null
                viewModel.startOver(withOriginalLines)
            },
            onDismiss = { dialog = null },
        )
        ConflictDialog.DiscardEdits -> {
            val prompt = discardEditsPrompt(viewModel.path)
            ConflictConfirmDialog(
                title = prompt.title,
                body = prompt.body,
                confirm = "Discard",
                danger = true,
                dismiss = "Keep editing",
                onConfirm = {
                    dialog = null
                    pendingLeave?.invoke()
                    pendingLeave = null
                },
                onDismiss = {
                    dialog = null
                    pendingLeave = null
                },
            )
        }
        null -> Unit
    }
}

/** `internal` so a test can compose every state without a ViewModel. */
@Composable
internal fun GitConflictContent(
    state: GitConflictUiState,
    path: String,
    editor: TextFieldState,
    onRetry: () -> Unit,
    onApply: () -> Unit,
    onSaveDraft: () -> Unit,
    onNextConflict: () -> Unit,
    onReloadFile: () -> Unit,
    onKeepMyEdits: () -> Unit,
    onTake: (TakeChoice) -> Unit,
    onClose: () -> Unit,
    modifier: Modifier = Modifier,
) {
    when (state) {
        is GitConflictUiState.Loading -> GitStateLoading(GIT_CONFLICT_LOADING_TAG, modifier)
        is GitConflictUiState.AgentOutdated -> GitStateMessage(
            title = "Update your agent",
            body = "This agent cannot resolve conflicts yet. Update it, or resolve them in the terminal.",
            tag = GIT_CONFLICT_OUTDATED_TAG,
            modifier = modifier,
        )
        is GitConflictUiState.SessionGone -> GitStateMessage(
            title = "Session ended",
            body = "This session is no longer running on the agent.",
            tag = GIT_CONFLICT_SESSION_GONE_TAG,
            modifier = modifier,
        )
        is GitConflictUiState.Error -> GitStateMessage(
            title = "Couldn't load the file",
            body = state.message,
            tag = GIT_CONFLICT_ERROR_TAG,
            actionLabel = "Retry",
            onAction = onRetry,
            modifier = modifier,
        )
        is GitConflictUiState.Ready -> Column(modifier) {
            ConflictHeader(path = path, caption = conflictCaption(state.conflict))
            if (state.gone) {
                NoticeStrip(
                    text = resolvedElsewhereNotice(path),
                    tag = GIT_CONFLICT_GONE_TAG,
                ) {
                    PixelTextButton(onClick = onClose) { Text("Close") }
                }
            }
            if (state.stale) {
                NoticeStrip(text = fileChangedNotice(path), tag = GIT_CONFLICT_STALE_TAG) {
                    PixelTextButton(
                        onClick = onReloadFile,
                        enabled = !state.busy,
                        modifier = Modifier.testTag(GIT_CONFLICT_RELOAD_TAG),
                    ) { Text("Reload file") }
                    PixelTextButton(
                        onClick = onKeepMyEdits,
                        enabled = !state.busy,
                        modifier = Modifier.testTag(GIT_CONFLICT_KEEP_EDITS_TAG),
                    ) { Text("Keep my edits") }
                }
            }
            if (state.editable) {
                ConflictEditor(
                    state = state,
                    editor = editor,
                    onNextConflict = onNextConflict,
                    modifier = Modifier.weight(1f),
                )
                ConflictActionBar(state = state, onApply = onApply, onSaveDraft = onSaveDraft)
            } else {
                BlockedConflict(state = state, onTake = onTake, modifier = Modifier.weight(1f))
            }
        }
    }
}

@Composable
private fun ConflictHeader(path: String, caption: String) {
    Column(
        modifier = Modifier
            .fillMaxWidth()
            .padding(horizontal = 16.dp, vertical = 8.dp),
        verticalArrangement = Arrangement.spacedBy(2.dp),
    ) {
        Text(
            text = path,
            style = MaterialTheme.typography.labelSmall.copy(fontFamily = FontFamily.Monospace),
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            maxLines = 2,
            overflow = TextOverflow.Ellipsis,
        )
        Text(text = caption, style = MaterialTheme.typography.bodySmall)
    }
    HorizontalDivider(color = MaterialTheme.pixel.surface2)
}

@Composable
private fun NoticeStrip(text: String, tag: String, actions: @Composable () -> Unit) {
    Column(
        modifier = Modifier
            .fillMaxWidth()
            .background(MaterialTheme.colorScheme.surfaceContainerHigh)
            .padding(start = 16.dp, end = 4.dp, top = 8.dp, bottom = 4.dp)
            .testTag(tag),
    ) {
        Text(
            text = text,
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.error,
            modifier = Modifier.padding(end = 12.dp),
        )
        Row(modifier = Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.End) { actions() }
    }
}

@Composable
private fun ConflictEditor(
    state: GitConflictUiState.Ready,
    editor: TextFieldState,
    onNextConflict: () -> Unit,
    modifier: Modifier = Modifier,
) {
    var legendOpen by rememberSaveable { mutableStateOf(false) }
    val focusRequester = remember { FocusRequester() }
    Column(modifier) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(start = 16.dp, end = 4.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text(
                text = conflictsLeftLabel(state.scan.conflicts, state.scan.hasMarkers),
                style = MaterialTheme.typography.labelMedium.copy(fontFamily = ChromeFontFamily),
                color = if (state.scan.hasMarkers) MaterialTheme.pixel.warning else MaterialTheme.pixel.ok,
                modifier = Modifier
                    .weight(1f)
                    .testTag(GIT_CONFLICT_COUNT_TAG),
            )
            PixelTextButton(
                onClick = {
                    focusRequester.requestFocus()
                    onNextConflict()
                },
                enabled = state.scan.conflicts > 0,
                modifier = Modifier.testTag(GIT_CONFLICT_NEXT_TAG),
            ) { Text("Next") }
            PixelTextButton(
                onClick = { legendOpen = !legendOpen },
                modifier = Modifier.testTag(GIT_CONFLICT_LEGEND_TOGGLE_TAG),
            ) { Text(if (legendOpen) "Hide sides" else "Which side?") }
        }
        if (legendOpen) {
            Column(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(horizontal = 16.dp, vertical = 4.dp)
                    .testTag(GIT_CONFLICT_LEGEND_TAG),
                verticalArrangement = Arrangement.spacedBy(4.dp),
            ) {
                for (line in conflictLegend(state.conflict, state.scan.hasBase)) {
                    Text(
                        text = line,
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }
        }
        if (state.overwriteWarning) {
            Text(
                text = KEEP_EDITS_WARNING,
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.error,
                modifier = Modifier.padding(horizontal = 16.dp, vertical = 2.dp),
            )
        }
        if (state.scan.tooLarge) {
            Text(
                text = tooLargeToSaveNotice(state.conflict.byte_limit),
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.error,
                modifier = Modifier
                    .padding(horizontal = 16.dp, vertical = 2.dp)
                    .testTag(GIT_CONFLICT_TOO_LARGE_TAG),
            )
        }
        HorizontalDivider(color = MaterialTheme.pixel.surface2)
        // Source code goes in verbatim: no autocorrect, no capitalisation.
        BasicTextField(
            state = editor,
            modifier = Modifier
                .fillMaxWidth()
                .weight(1f)
                .background(MaterialTheme.colorScheme.background)
                .padding(horizontal = 12.dp, vertical = 8.dp)
                .focusRequester(focusRequester)
                .testTag(GIT_CONFLICT_EDITOR_TAG),
            readOnly = state.busy || state.gone,
            textStyle = MaterialTheme.typography.bodySmall.copy(
                fontFamily = FontFamily.Monospace,
                color = MaterialTheme.colorScheme.onBackground,
            ),
            keyboardOptions = KeyboardOptions(
                capitalization = KeyboardCapitalization.None,
                autoCorrectEnabled = false,
            ),
            lineLimits = TextFieldLineLimits.MultiLine(),
            cursorBrush = SolidColor(MaterialTheme.colorScheme.primary),
        )
    }
}

@Composable
private fun ConflictActionBar(
    state: GitConflictUiState.Ready,
    onApply: () -> Unit,
    onSaveDraft: () -> Unit,
) {
    HorizontalDivider(color = MaterialTheme.pixel.surface2)
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .padding(horizontal = 12.dp, vertical = 8.dp),
        horizontalArrangement = Arrangement.spacedBy(8.dp, Alignment.End),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        PixelTextButton(
            onClick = onSaveDraft,
            enabled = state.canSaveDraft,
            modifier = Modifier.testTag(GIT_CONFLICT_DRAFT_TAG),
        ) { Text("Save draft") }
        PixelButton(
            text = if (state.busy) "Saving…" else "Apply & mark resolved",
            onClick = onApply,
            style = PixelButtonStyle.Primary,
            enabled = state.canApply,
            modifier = Modifier.testTag(GIT_CONFLICT_APPLY_TAG),
        )
    }
}

@Composable
private fun BlockedConflict(
    state: GitConflictUiState.Ready,
    onTake: (TakeChoice) -> Unit,
    modifier: Modifier = Modifier,
) {
    Column(
        modifier = modifier
            .fillMaxWidth()
            .verticalScroll(rememberScrollState())
            .padding(16.dp)
            .testTag(GIT_CONFLICT_BLOCKED_TAG),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Text(
            text = conflictKindNotice(state.conflict).orEmpty(),
            style = MaterialTheme.typography.bodyMedium,
        )
        for (choice in takeChoices(state.conflict)) {
            PixelButton(
                text = "${choice.label}…",
                onClick = { onTake(choice) },
                style = if (choice.danger) PixelButtonStyle.Danger else PixelButtonStyle.Ghost,
                enabled = !state.busy && !state.gone,
                modifier = Modifier
                    .fillMaxWidth()
                    .testTag(gitConflictTakeTag(choice.side)),
            )
        }
    }
}

@Composable
private fun ConflictMenu(
    conflict: GitConflictResponse,
    enabled: Boolean,
    onTake: (TakeChoice) -> Unit,
    onStartOver: () -> Unit,
    onViewDiff: () -> Unit,
) {
    var open by remember { mutableStateOf(false) }
    Box {
        IconButton(onClick = { open = true }, modifier = Modifier.testTag(GIT_CONFLICT_MENU_TAG)) {
            Icon(Icons.Default.MoreVert, contentDescription = "More conflict actions")
        }
        DropdownMenu(expanded = open, onDismissRequest = { open = false }) {
            // A conflict that cannot be edited shows its picks in the body instead.
            if (isEditableConflict(conflict)) {
                for (choice in takeChoices(conflict)) {
                    DropdownMenuItem(
                        text = { Text("${choice.label}…") },
                        enabled = enabled,
                        onClick = {
                            open = false
                            onTake(choice)
                        },
                        modifier = Modifier.testTag(gitConflictTakeTag(choice.side)),
                    )
                }
            }
            if (canStartOver(conflict)) {
                DropdownMenuItem(
                    text = { Text("$START_OVER_LABEL…") },
                    enabled = enabled,
                    onClick = {
                        open = false
                        onStartOver()
                    },
                    modifier = Modifier.testTag(GIT_CONFLICT_START_OVER_TAG),
                )
            }
            DropdownMenuItem(
                text = { Text("View diff") },
                onClick = {
                    open = false
                    onViewDiff()
                },
                modifier = Modifier.testTag(GIT_CONFLICT_VIEW_DIFF_TAG),
            )
        }
    }
}

@Composable
private fun ConflictConfirmDialog(
    title: String,
    body: String,
    confirm: String,
    danger: Boolean,
    onConfirm: () -> Unit,
    onDismiss: () -> Unit,
    dismiss: String = "Cancel",
) {
    PixelAlertDialog(
        onDismissRequest = onDismiss,
        title = { Text(title) },
        text = { Text(body) },
        confirmButton = {
            PixelTextButton(onClick = onConfirm) {
                Text(
                    text = confirm,
                    color = if (danger) MaterialTheme.colorScheme.error else MaterialTheme.colorScheme.primary,
                )
            }
        },
        dismissButton = { PixelTextButton(onClick = onDismiss) { Text(dismiss) } },
    )
}

@Composable
private fun StartOverDialog(
    prompt: ConflictPrompt,
    onStartOver: (withOriginalLines: Boolean) -> Unit,
    onDismiss: () -> Unit,
) {
    PixelAlertDialog(
        onDismissRequest = onDismiss,
        title = { Text(prompt.title) },
        text = { Text(prompt.body) },
        confirmButton = {
            Column(horizontalAlignment = Alignment.End) {
                PixelTextButton(onClick = { onStartOver(false) }) {
                    Text(START_OVER_LABEL, color = MaterialTheme.colorScheme.error)
                }
                PixelTextButton(onClick = { onStartOver(true) }) {
                    Text(START_OVER_WITH_BASE_LABEL, color = MaterialTheme.colorScheme.error)
                }
                PixelTextButton(onClick = onDismiss) { Text("Cancel") }
            }
        },
    )
}

private val PREVIEW_CONFLICT = GitConflictResponse(
    path = "src/login.py",
    code = "UU",
    state = GitRepoInfo.STATE_REBASING,
    kind = GitConflictResponse.KIND_TEXT,
    content = "def login():\n<<<<<<< HEAD\n    return home\n=======\n    return next_url\n>>>>>>> fedcba9\n",
    token = "t",
    markers = 1,
    ours = GitConflictSide("main", present = true),
    theirs = GitConflictSide("fedcba9 Fix the login redirect", present = true),
)

@Composable
private fun GitConflictPreview(dark: Boolean, conflict: GitConflictResponse) {
    MobaideTheme(darkTheme = dark) {
        GitConflictContent(
            state = GitConflictUiState.Ready(
                conflict = conflict,
                scan = scanConflictText(conflict.content.orEmpty()),
                stale = conflict.kind == GitConflictResponse.KIND_TEXT,
            ),
            path = conflict.path,
            editor = remember { TextFieldState(conflict.content.orEmpty()) },
            onRetry = {},
            onApply = {},
            onSaveDraft = {},
            onNextConflict = {},
            onReloadFile = {},
            onKeepMyEdits = {},
            onTake = {},
            onClose = {},
        )
    }
}

@Preview(name = "Conflict editor dark", showBackground = true, backgroundColor = 0xFF121212)
@Composable
private fun GitConflictEditorPreviewDark() = GitConflictPreview(dark = true, conflict = PREVIEW_CONFLICT)

@Preview(name = "Conflict editor light", showBackground = true, backgroundColor = 0xFFF4F4F4)
@Composable
private fun GitConflictEditorPreviewLight() = GitConflictPreview(dark = false, conflict = PREVIEW_CONFLICT)

@Preview(name = "Conflict binary dark", showBackground = true, backgroundColor = 0xFF121212)
@Composable
private fun GitConflictBinaryPreviewDark() = GitConflictPreview(
    dark = true,
    conflict = PREVIEW_CONFLICT.copy(
        path = "assets/logo.png",
        kind = GitConflictResponse.KIND_BINARY,
        content = null,
        token = null,
    ),
)

@Preview(name = "Conflict binary light", showBackground = true, backgroundColor = 0xFFF4F4F4)
@Composable
private fun GitConflictBinaryPreviewLight() = GitConflictPreview(
    dark = false,
    conflict = PREVIEW_CONFLICT.copy(
        path = "assets/logo.png",
        kind = GitConflictResponse.KIND_BINARY,
        content = null,
        token = null,
    ),
)
