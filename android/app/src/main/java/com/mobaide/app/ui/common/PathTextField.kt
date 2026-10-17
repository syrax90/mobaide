package com.mobaide.app.ui.common

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.Folder
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.onFocusChanged
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.text.TextRange
import androidx.compose.ui.text.input.KeyboardCapitalization
import androidx.compose.ui.text.input.TextFieldValue
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import com.mobaide.app.data.fs.PathCompletion
import com.mobaide.app.data.repository.PathSuggestionRepository
import com.mobaide.app.ui.common.pixel.PixelCard
import com.mobaide.app.ui.common.pixel.PixelTextFieldDefaults
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.distinctUntilChanged
import kotlinx.coroutines.flow.collectLatest
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch

/** What the path field's suggestion list shows; [listedPath] is where [names] live. */
data class PathSuggestionsUi(
    val names: List<String> = emptyList(),
    val overflow: Int = 0,
    val listedPath: String = "",
) {
    companion object {
        val None = PathSuggestionsUi()
    }
}

/**
 * Drives one host-path field's folder suggestions inside a ViewModel's scope.
 *
 * The fetch pipeline keys on the *request* (`dir`, `q`), not on the text, so
 * keystrokes inside a directory already being listed leave the in-flight
 * listing alone, while the rendered list re-filters the cache on every one.
 */
class PathSuggestionsController(
    private val repository: PathSuggestionRepository,
    private val scope: CoroutineScope,
) {
    private data class Input(val text: String, val focused: Boolean)

    private val input = MutableStateFlow(Input("", focused = false))
    private val supported = MutableStateFlow(false)
    private val revision = MutableStateFlow(0)
    private var probe: Job? = null

    private val query = combine(input, supported) { current, isSupported ->
        if (isSupported && current.focused) PathCompletion.split(current.text) else null
    }.distinctUntilChanged()

    val state: StateFlow<PathSuggestionsUi> = combine(query, revision) { current, _ ->
        render(current)
    }.stateIn(scope, SharingStarted.Eagerly, PathSuggestionsUi.None)

    init {
        scope.launch {
            combine(query, revision) { current, _ -> current?.let(repository::nextRequest) }
                .distinctUntilChanged()
                .collectLatest { request ->
                    if (request == null) return@collectLatest
                    delay(PathCompletion.DEBOUNCE_MS)
                    if (repository.fetch(request)) revision.update { it + 1 }
                }
        }
    }

    fun onTextChange(text: String) = input.update { it.copy(text = text) }

    fun onFocusChange(focused: Boolean) {
        input.update { it.copy(focused = focused) }
        if (focused && !supported.value && probe?.isActive != true) {
            probe = scope.launch { supported.value = repository.isSupported() }
        }
    }

    /** The field's new text for a picked suggestion. */
    fun accept(name: String): String = PathCompletion.accept(state.value.listedPath, name)

    private fun render(current: PathCompletion.Query?): PathSuggestionsUi {
        if (current == null) return PathSuggestionsUi.None
        val hit = repository.lookup(current) ?: return PathSuggestionsUi.None
        val ranked = PathCompletion.rank(hit.listing.dirs, current.fragment)
        return PathSuggestionsUi(
            names = ranked.take(PathCompletion.MAX_VISIBLE_SUGGESTIONS),
            overflow = (ranked.size - PathCompletion.MAX_VISIBLE_SUGGESTIONS).coerceAtLeast(0),
            listedPath = hit.listing.path,
        )
    }
}

/**
 * A host-path [OutlinedTextField] with folder suggestions listed in flow under
 * it — not a popup, so nothing fights the IME or a bottom sheet for focus.
 * Picking a folder writes `<dir>/<name>/` with the caret at the end, so the
 * next level's folders appear straight away.
 */
@Composable
fun PathTextField(
    value: String,
    onValueChange: (String) -> Unit,
    suggestions: PathSuggestionsUi,
    onFocusChange: (Boolean) -> Unit,
    onPickSuggestion: (String) -> Unit,
    label: @Composable () -> Unit,
    isError: Boolean,
    supportingText: @Composable (() -> Unit)?,
    modifier: Modifier = Modifier,
) {
    // A String-valued field keeps its old caret when the value is replaced from
    // outside, which would strand it mid-path after a pick; an external change
    // is shown with the caret at the end instead.
    var fieldValue by remember { mutableStateOf(TextFieldValue(value, TextRange(value.length))) }
    val shown = if (fieldValue.text == value) fieldValue else TextFieldValue(value, TextRange(value.length))
    Column(modifier = Modifier.fillMaxWidth(), verticalArrangement = Arrangement.spacedBy(4.dp)) {
        OutlinedTextField(
            colors = PixelTextFieldDefaults.colors(),
            value = shown,
            onValueChange = {
                fieldValue = it
                if (it.text != value) onValueChange(it.text)
            },
            label = label,
            singleLine = true,
            keyboardOptions = KeyboardOptions(
                capitalization = KeyboardCapitalization.None,
                autoCorrectEnabled = false,
            ),
            isError = isError,
            supportingText = supportingText,
            modifier = modifier
                .fillMaxWidth()
                .onFocusChanged { onFocusChange(it.isFocused) },
        )
        if (suggestions.names.isNotEmpty()) {
            PixelCard(modifier = Modifier.fillMaxWidth().testTag("path_suggestions")) {
                suggestions.names.forEach { name ->
                    Row(
                        verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.spacedBy(8.dp),
                        modifier = Modifier
                            .fillMaxWidth()
                            .heightIn(min = 48.dp)
                            .clickable(role = Role.Button) { onPickSuggestion(name) }
                            .padding(horizontal = 12.dp),
                    ) {
                        Icon(
                            imageVector = Icons.Outlined.Folder,
                            contentDescription = null,
                            modifier = Modifier.size(18.dp),
                            tint = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                        Text(
                            text = "$name/",
                            maxLines = 1,
                            overflow = TextOverflow.Ellipsis,
                            style = MaterialTheme.typography.bodyMedium,
                        )
                    }
                }
                if (suggestions.overflow > 0) {
                    Text(
                        text = "+${suggestions.overflow} more — keep typing",
                        style = MaterialTheme.typography.labelMedium,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        modifier = Modifier.padding(horizontal = 12.dp, vertical = 8.dp),
                    )
                }
            }
        }
    }
}

const val PATH_BROWSE_HINT = "Type / or ~ to browse folders on the agent"
