# Testing a Screen

Each screen needs a Compose UI test that exercises every `UiState` branch (`Loading`, `Empty`, `Success`, `Error`) plus the primary action. Tests use a **fake Repository** that exposes a `MutableStateFlow` you push values into — no mocks, no network.

## Fake repository

```kotlin
class FakeFooRepository : FooRepository(
    restApi = error("not used"),
    eventBus = error("not used"),
) {
    private val state = MutableStateFlow<List<FooDto>>(emptyList())
    override fun observeFoos(): StateFlow<List<FooDto>> = state.asStateFlow()
    fun emit(list: List<FooDto>) { state.value = list }

    var renameError: Throwable? = null
    val renamed = mutableListOf<Pair<String, String>>()
    override suspend fun renameFoo(id: String, newName: String, idempotencyKey: String) {
        renameError?.let { throw it }
        renamed += id to newName
        state.update { it.map { f -> if (f.id == id) f.copy(name = newName) else f } }
    }
}
```

If the real `FooRepository` is `final`, extract an interface (`FooRepository` interface + `FooRepositoryImpl`) — the skill prefers an interface for any repository that has a UI test.

## Compose UI test

```kotlin
@HiltAndroidTest
class FooScreenTest {

    @get:Rule(order = 0) val hilt = HiltAndroidRule(this)
    @get:Rule(order = 1) val compose = createAndroidComposeRule<HiltTestActivity>()

    @Inject lateinit var fakeRepo: FakeFooRepository

    @Before fun setup() { hilt.inject() }

    @Test fun loading_state_shows_indicator() {
        compose.setContent { FooScreen(onNavigateToDetail = {}) }
        compose.onNodeWithTag("foo_loading").assertIsDisplayed()
    }

    @Test fun empty_state_shows_placeholder() {
        fakeRepo.emit(emptyList())
        compose.setContent { FooScreen(onNavigateToDetail = {}) }
        compose.onNodeWithTag("foo_empty").assertIsDisplayed()
    }

    @Test fun success_state_renders_items() {
        fakeRepo.emit(listOf(FooDto(id = "1", name = "alpha")))
        compose.setContent { FooScreen(onNavigateToDetail = {}) }
        compose.onNodeWithText("alpha").assertIsDisplayed()
    }

    @Test fun click_navigates() {
        fakeRepo.emit(listOf(FooDto(id = "1", name = "alpha")))
        var navigatedTo: String? = null
        compose.setContent { FooScreen(onNavigateToDetail = { navigatedTo = it }) }
        compose.onNodeWithText("alpha").performClick()
        compose.runOnIdle { assertEquals("1", navigatedTo) }
    }

    @Test fun mutation_failure_shows_snackbar() {
        fakeRepo.emit(listOf(FooDto(id = "1", name = "alpha")))
        fakeRepo.renameError = IllegalStateException("boom")
        compose.setContent { FooScreen(onNavigateToDetail = {}) }
        compose.onNodeWithTag("rename_button_1").performClick()
        compose.onNodeWithText("Rename failed: boom").assertIsDisplayed()
    }
}
```

## Required `testTag` contract

Every screen MUST set these tags so tests are uniform:

| Tag | Where |
|-----|-------|
| `<feature>_screen` | Root `Scaffold` / `Box` |
| `<feature>_loading` | `CircularProgressIndicator` in the `Loading` branch |
| `<feature>_empty` | Placeholder in the `Empty` branch |
| `<feature>_error` | Error text in the `Error` branch |
| `<action>_button[_<id>]` | Any button that triggers a server action |

## What NOT to do

- Don't mock `RestApi` directly — the test should be screen-level, not network-level. Use a fake Repository.
- Don't `Thread.sleep` — use `compose.waitUntil { ... }` or `runOnIdle`.
- Don't test `ViewModel` and screen separately when one Compose test can cover both — the screen + its ViewModel are the unit.
- Don't assert against rendered pixels for logic — use `testTag` + `onNodeWithText`. Reserve screenshot tests (Roborazzi/Paparazzi) for visual regression of `ui/common/` components only.
