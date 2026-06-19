# Server-Sync Patterns

Three recipes for "button click → server → UI update". Every server-touching action in this app must match one of them. They all rely on the **Repository = single source of truth** invariant: the UI observes the Repository's `StateFlow`, and the Repository merges REST responses with `ServerEvent` pushes from `ServerEventBus`.

The canonical existing example is `data/repository/SessionRepository.kt` — read it once before writing a new repo.

---

## (A) Optimistic mutation + WS reconcile — DEFAULT

Use for non-destructive mutations (toggle, rename, create) where the server will emit a matching `ServerEvent` and the user expects instant feedback.

### Repository

```kotlin
class FooRepository(
    private val restApi: RestApi,
    private val eventBus: ServerEventBus,
) {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)
    private val _foos = MutableStateFlow<List<FooDto>>(emptyList())
    fun observeFoos(): StateFlow<List<FooDto>> = _foos.asStateFlow()

    init {
        // WS push reconciles the cache. This is what makes the UI "auto-refresh".
        scope.launch {
            eventBus.stream<ServerEvent.FoosChanged>().collect { ev ->
                _foos.value = ev.foos
            }
        }
    }

    suspend fun renameFoo(id: String, newName: String, idempotencyKey: String) {
        // 1. Optimistic local update.
        val snapshot = _foos.value
        _foos.update { list -> list.map { if (it.id == id) it.copy(name = newName) else it } }
        try {
            // 2. REST. Server emits FoosChanged → handler above reconciles.
            restApi.renameFoo(id, RenameRequest(newName), idempotencyKey)
        } catch (e: Exception) {
            // 3. Revert on failure. The ViewModel will surface a Retry snackbar.
            _foos.value = snapshot
            throw e
        }
    }
}
```

### ViewModel

```kotlin
private val renameIntents = MutableSharedFlow<RenameIntent>(extraBufferCapacity = 16)

init {
    viewModelScope.launch {
        // Debounce rapid clicks; only the latest survives.
        renameIntents.collectLatest { intent ->
            try {
                repo.renameFoo(intent.id, intent.name, idempotencyKey = UUID.randomUUID().toString())
            } catch (e: Exception) {
                _events.send(FooEvent.ShowRetry("Rename failed", retry = { onRename(intent) }))
            }
        }
    }
}

fun onRename(intent: RenameIntent) { renameIntents.tryEmit(intent) }
```

---

## (B) Pessimistic destructive mutation

Use for delete / force-stop / anything irreversible. The user MUST see a spinner and an explicit success/failure — no optimism.

### ViewModel

```kotlin
fun onDelete(id: String) {
    viewModelScope.launch {
        _localOverlay.update { it.copy(deleting = id) }   // disables button, shows spinner
        try {
            repo.deleteFoo(id, idempotencyKey = UUID.randomUUID().toString())
            // No manual refetch: the WS FoosChanged push removes it from repo._foos.
        } catch (e: Exception) {
            _events.send(FooEvent.ShowMessage("Delete failed: ${e.message}"))
        } finally {
            _localOverlay.update { it.copy(deleting = null) }
        }
    }
}
```

`_localOverlay` is a small `MutableStateFlow` for *transient* per-row UI flags (deleting, submitting). It is **not** the screen's `UiState` — it's combined with the repo flow inside `stateIn`.

---

## (C) Fetch-only refresh (pull-to-refresh, manual reload)

Use when the user explicitly asks to refresh and there is no mutation involved.

### Repository

```kotlin
suspend fun refresh() {
    val fresh = restApi.listFoos()
    _foos.value = fresh   // WS push will reconcile any drift afterwards
}
```

### ViewModel

```kotlin
fun onPullToRefresh() {
    viewModelScope.launch {
        try { repo.refresh() }
        catch (e: Exception) { _events.send(FooEvent.ShowMessage("Refresh failed: ${e.message}")) }
    }
}
```

Wire to `Material3 PullToRefreshBox` in the screen.

---

## Required hygiene for ALL three patterns

- **Idempotency key:** generate `UUID.randomUUID().toString()` per mutation, send as a header or body field. The server uses it to dedupe retries — without it, an optimistic-update + retry can apply twice.
- **Debounce:** any button that fires a mutation must funnel through `collectLatest` on an intent flow, OR be disabled in UiState while in flight. Never bind a raw `onClick` directly to a suspending repo call without one of the two.
- **Failure UX:** every catch surfaces a snackbar with a Retry action via `FooEvent.ShowRetry`. Silent failures are forbidden — they cause "I clicked the button but nothing happened" bug reports.
- **No manual polling.** If you find yourself writing `while (true) { delay(...); repo.refresh() }`, stop and add a `ServerEvent` instead — see `server_event_bus.md`.
