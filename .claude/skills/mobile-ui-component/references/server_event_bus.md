# Adding a New ServerEvent Type

When a UI screen depends on a server-owned entity that doesn't yet have a push channel, add a new `ServerEvent` rather than polling. This is what makes the "UI auto-refreshes after server changes" guarantee actually hold.

The canonical example is `SessionsChanged` / `SessionUpdated` in `data/events/ServerEvent.kt`, parsed in `data/events/ServerEventBus.kt`, and consumed in `data/repository/SessionRepository.kt`. Mirror it.

## Steps

### 1. Add the sealed subclass

`android/app/src/main/java/com/claudemobile/app/data/events/ServerEvent.kt`

```kotlin
sealed class ServerEvent {
    // ... existing ...
    data class FoosChanged(val foos: List<FooDto>) : ServerEvent()
}

@Serializable
data class FoosChangedPayload(val foos: List<FooDto>)
```

`FooDto` must be `@Serializable` and live in `data/network/dto/`.

### 2. Parse the wire envelope

`android/app/src/main/java/com/claudemobile/app/data/events/ServerEventBus.kt` — add a branch to `parseAndEmit`:

```kotlin
"foos.changed" -> ServerEvent.FoosChanged(
    json.decodeFromJsonElement(FoosChangedPayload.serializer(), payload).foos
)
```

The `type` string MUST exactly match what the Python server sends in `{"type": "...", "payload": {...}}`. Coordinate the name with the server contract before merging.

### 3. Repository subscribes in `init`

`android/app/src/main/java/com/claudemobile/app/data/repository/FooRepository.kt`:

```kotlin
class FooRepository(
    private val restApi: RestApi,
    private val eventBus: ServerEventBus,
) {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)
    private val _foos = MutableStateFlow<List<FooDto>>(emptyList())
    fun observeFoos(): StateFlow<List<FooDto>> = _foos.asStateFlow()

    init {
        scope.launch {
            eventBus.stream<ServerEvent.FoosChanged>().collect { ev ->
                _foos.value = ev.foos
            }
        }
    }

    suspend fun listFoos(): List<FooDto> = restApi.listFoos().also { _foos.value = it }
}
```

`stream<T>()` is the existing inline reified helper on `ServerEventBus` — use it; don't write your own `filterIsInstance` chain.

### 4. Provide it in Hilt

`android/app/src/main/java/com/claudemobile/app/di/RepositoryModule.kt` — add a `@Provides @Singleton` factory matching the existing repository entries.

### 5. Server side (Python FastAPI)

The backend MUST emit the matching envelope on `ws/events` whenever the underlying state changes. Without this, the WS reconcile path is dead and you fall back to manual refetch — which violates the skill's server-sync rule. Verify the server emits the new event type before marking the task done.

## Checklist

- [ ] `FooDto` is `@Serializable` and lives in `data/network/dto/`
- [ ] New `ServerEvent.FoosChanged` subclass added
- [ ] New `FoosChangedPayload` `@Serializable` wrapper added
- [ ] `ServerEventBus.parseAndEmit` has a branch for the new `type` string
- [ ] Repository subscribes in `init` and updates its `MutableStateFlow`
- [ ] Hilt module provides the repository
- [ ] Python server emits the matching envelope
- [ ] Manually verified: trigger a state change on the server, see the UI re-render with no user action
