# Screen Template

Copy-paste skeleton for a new screen. Replace `Foo` with the feature name. Lives at `android/app/src/main/java/com/claudemobile/app/ui/foo/`.

## `FooUiState.kt`

```kotlin
package com.claudemobile.app.ui.foo

import com.claudemobile.app.data.network.dto.FooDto

sealed interface FooUiState {
    data object Loading : FooUiState
    data object Empty : FooUiState
    data class Success(val items: List<FooDto>, val refreshing: Boolean = false) : FooUiState
    data class Error(val message: String) : FooUiState
}

/** One-shot side effects — navigation, toasts. Never put these in UiState. */
sealed interface FooEvent {
    data class NavigateToDetail(val id: String) : FooEvent
    data class ShowMessage(val text: String) : FooEvent
}
```

## `FooViewModel.kt`

```kotlin
package com.claudemobile.app.ui.foo

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.claudemobile.app.data.repository.FooRepository
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.catch
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.flow.receiveAsFlow
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.launch
import javax.inject.Inject

@HiltViewModel
class FooViewModel @Inject constructor(
    private val repo: FooRepository,
) : ViewModel() {

    val uiState: StateFlow<FooUiState> = repo.observeFoos()
        .map<_, FooUiState> { list ->
            if (list.isEmpty()) FooUiState.Empty
            else FooUiState.Success(list)
        }
        .catch { e -> emit(FooUiState.Error(e.message ?: "Unknown error")) }
        .stateIn(viewModelScope, SharingStarted.WhileSubscribed(5_000), FooUiState.Loading)

    private val _events = Channel<FooEvent>(Channel.BUFFERED)
    val events = _events.receiveAsFlow()

    fun onItemClick(id: String) {
        viewModelScope.launch { _events.send(FooEvent.NavigateToDetail(id)) }
    }

    fun onRefresh() {
        viewModelScope.launch {
            try {
                repo.refresh()  // hits REST; WS push reconciles via repo.init subscription
            } catch (e: Exception) {
                _events.send(FooEvent.ShowMessage("Refresh failed: ${e.message}"))
            }
        }
    }
}
```

## `FooScreen.kt`

```kotlin
package com.claudemobile.app.ui.foo

import androidx.compose.foundation.layout.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.tooling.preview.Preview
import androidx.hilt.navigation.compose.hiltViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.compose.foundation.layout.WindowInsets
import kotlinx.coroutines.flow.collectLatest

@Composable
fun FooScreen(
    onNavigateToDetail: (String) -> Unit,
    modifier: Modifier = Modifier,
    viewModel: FooViewModel = hiltViewModel(),
) {
    val state by viewModel.uiState.collectAsStateWithLifecycle()
    val snackbarHostState = remember { SnackbarHostState() }

    LaunchedEffect(viewModel) {
        viewModel.events.collectLatest { event ->
            when (event) {
                is FooEvent.NavigateToDetail -> onNavigateToDetail(event.id)
                is FooEvent.ShowMessage -> snackbarHostState.showSnackbar(event.text)
            }
        }
    }

    // Errors from UiState are surfaced through the same SnackbarHost.
    LaunchedEffect(state) {
        (state as? FooUiState.Error)?.let { snackbarHostState.showSnackbar(it.message) }
    }

    Scaffold(
        modifier = modifier.testTag("foo_screen"),
        snackbarHost = { SnackbarHost(snackbarHostState) },
    ) { inner ->
        Box(Modifier.padding(inner).fillMaxSize()) {
            when (val s = state) {
                FooUiState.Loading -> CircularProgressIndicator(Modifier.testTag("foo_loading"))
                FooUiState.Empty -> Text("Nothing here yet", Modifier.testTag("foo_empty"))
                is FooUiState.Success -> FooList(items = s.items, onItemClick = viewModel::onItemClick)
                is FooUiState.Error -> Text(s.message, Modifier.testTag("foo_error"))
            }
        }
    }
}

@Composable
private fun FooList(items: List<com.claudemobile.app.data.network.dto.FooDto>, onItemClick: (String) -> Unit) {
    // ... stateless rows ...
}

@Preview(showBackground = true) @Composable
private fun FooScreenPreviewLight() = MaterialTheme { /* fake state */ }

@Preview(showBackground = true, uiMode = android.content.res.Configuration.UI_MODE_NIGHT_YES) @Composable
private fun FooScreenPreviewDark() = MaterialTheme { /* fake state */ }
```

## Notes

- The ViewModel does not own a `MutableStateFlow<UiState>`. It maps the **Repository's** flow into UiState, so the moment a `ServerEvent` arrives, the UI re-renders with no extra glue.
- One-shot events use `Channel`, not `SharedFlow` — the latter replays on configuration change and double-fires navigation.
- `testTag`s are required on `Loading`, `Empty`, `Error`, and the root — they are the contract `references/testing.md` relies on.
