# Contributing to Mobaide

Thank you for helping to make Mobaide better. This page tells you how to set up the code, test a change and open a pull request.

**On this page**

- [Before you start](#before-you-start)
- [Set up your environment](#set-up-your-environment)
- [Run the tests](#run-the-tests)
- [Rules for changes](#rules-for-changes)
- [Open a pull request](#open-a-pull-request)
- [License](#license)

## Before you start

| You want to | Go to |
|---|---|
| Ask a question or share an idea | [Community discussions](https://github.com/syrax90/mobaide-community/discussions) |
| Report a bug | A new [issue](https://github.com/syrax90/claude_mobile/issues) with the steps, the platform and the app and agent versions |
| Make a large change | An issue first, so that we agree on the approach before you write the code |

Small fixes, such as typos and clear bugs, can go straight to a pull request.

To learn how the parts fit, read [architecture.md](docs/architecture.md). [docs/doc-list.md](docs/doc-list.md) lists every document with a short description.

## Set up your environment

Fork the repository and clone your fork. Then set up only the parts you change.

| Part | You need | Commands |
|---|---|---|
| Agent and relay (`mobaide_agent/`, `central_server/`) | Python 3.12+ | `python -m pip install -e 'mobaide_agent[dev]' -e 'central_server[dev]'` |
| Web client (`web/`) | Node 22 (22.22.2+), 24 (24.15+) or 26+ | `cd web && npm ci && npm run dev` |
| Android app (`android/`) | Android Studio and its bundled JDK | `cd android && ./gradlew :app:assembleDebug` |
| iOS app (`ios/`) | macOS with Xcode | Open `ios/Mobaide.xcodeproj` |

Use a virtual environment for Python. If you also run an installed Mobaide agent, keep the two separate.

### Run everything on your computer

`tests/e2e/manual_stack.py` starts a local relay and a local agent, and connects the apps to them:

```sh
python tests/e2e/manual_stack.py up          # relay + agent
python tests/e2e/manual_stack.py up --web    # also serve the web client
python tests/e2e/manual_stack.py stop
```

It prints the test account to sign in with. [manual-e2e-testing.md](docs/manual-e2e-testing.md) tells how to connect a phone, an emulator or a simulator.

> [!IMPORTANT]
> Test against your local stack only. Do not test against mobaide.com.

## Run the tests

CI runs only some of the suites. Before you open a pull request, run the suites for the parts you changed.

| Part | Command |
|---|---|
| Agent | `cd mobaide_agent && python -m pytest` |
| Relay | `cd central_server && python -m pytest` |
| Install scripts and protocol checks | `python -m pytest tests --ignore=tests/e2e` |
| End-to-end (real relay and agent) | `python -m pytest tests/e2e/ -v` |
| Web | `cd web && npm run lint && npm run typecheck && npm test` |
| Android | `cd android && ./gradlew :app:testDebugUnitTest` |
| iOS | `xcodebuild -project ios/Mobaide.xcodeproj -scheme Mobaide -destination 'platform=iOS Simulator,name=iPhone 16' CODE_SIGNING_ALLOWED=NO test` |

If a test also fails on `main`, say so in the pull request.

## Rules for changes

- **Keep the apps in sync.** Android, iOS and the web client must work the same way. If you change one of them, also change the other two, or say in the pull request which ones still need the change. Exceptions are things that only exist on one platform, such as OS APIs or store rules.
- **Wire protocol.** A change to the messages between the apps, the relay and the agent must follow [PROTOCOL.md](PROTOCOL.md), section "How to bump". The shared test fixtures are in `tests/protocol_fixtures/`.
- **Agent API.** If you change an agent API that the root `tests/` also use, update both `mobaide_agent/tests/` and `tests/`.
- **Documentation.** Update the docs that your change makes wrong. When you add, rename or remove a document, update [docs/doc-list.md](docs/doc-list.md).
- **Public claims.** Text in the README or on the website must pass the accuracy gate in [website.md](docs/website.md).
- **Brand logos.** Do not add logos of Claude, Codex, Antigravity or other products. Use their names as text.

## Open a pull request

1. Create a branch from `main`. Keep one topic in each pull request.
2. Give the pull request a title in the [Conventional Commits](https://www.conventionalcommits.org/) format. A check enforces it.
   - **Types:** `feat`, `fix`, `docs`, `refactor`, `perf`, `test`, `build`, `ci`, `chore`, `style`, `revert`.
   - **Scopes (optional):** `agent`, `server`, `android`, `ios`, `web`, `protocol`, `infra`, `docs`.
   - **Examples:** `fix(android): keep the cursor visible after rotation`, `feat(web): add a session search box`.
3. In the description, say what changed, why, and how you tested it. For a UI change, add screenshots.
4. The maintainer reviews the pull request and merges it as one squashed commit.

The title becomes the commit message on `main`. The release tooling reads it to write the changelogs and choose the next version.

Changes to `.github/workflows/`, build scripts, install scripts or lockfiles get a stricter review. After the merge, they run on the maintainer's machine with the release keys, or go live to users at once.

## License

Mobaide is licensed under the [Apache License 2.0](LICENSE).

When you submit a contribution, you agree that it is licensed under the same license, as section 5 of the license says. There is no separate contributor agreement. Submit only work that you have the right to submit.

The license does not give permission to use the name "Mobaide" or its logo and icons. If you publish a fork, give it a different name and icon.
