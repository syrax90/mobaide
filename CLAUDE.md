# Mobaide (Claude Mobile)

Multi-component repo: Android client (`android/`), iOS client (`ios/`), web client (`web/`), Python agent (`mobaide_agent/`), Python relay server (`central_server/`), install scripts (`deploy/`), and environment profiles (`config/`).

## Documentation

Before loading any documentation file, **read `docs/doc-list.md` first**. It is a registry of every documentation file in this repo, each with a one-line description of its purpose and scope. Use it to load only the specific doc(s) the current task needs — do not pre-load the whole `docs/` folder, it wastes context.

When you add, rename, or remove a documentation file, update `docs/doc-list.md` to keep the registry in sync.

## Cross-Platform Parity

Android (`android/`), iOS (`ios/`), and the web client (`web/`) must stay functionally in sync:

- Every client-facing change on one platform must be mirrored to the other two — except platform-specific cases (OS APIs, platform UI/UX conventions, store requirements, browser constraints).
- If a mirror is not implemented immediately, a dedicated Linear issue for each missing platform must already exist or be created before the PR merges.
- The web client is still being built out milestone by milestone (WEB-01…WEB-13, CLA-188…CLA-200); until a feature area exists there, its mirror is covered by the corresponding planned WEB-* issue and no extra issue is needed.
