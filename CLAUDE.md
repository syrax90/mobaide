# Mobaide (Claude Mobile)

Multi-component repo: Android client (`android/`), Python agent (`mobaide_agent/`), Python relay server (`central_server/`), install scripts (`deploy/`), and environment profiles (`config/`).

## Documentation

Before loading any documentation file, **read `docs/doc-list.md` first**. It is a registry of every documentation file in this repo, each with a one-line description of its purpose and scope. Use it to load only the specific doc(s) the current task needs — do not pre-load the whole `docs/` folder, it wastes context.

When you add, rename, or remove a documentation file, update `docs/doc-list.md` to keep the registry in sync.

## Cross-Platform Parity

Android (`android/`) and iOS (`ios/`) must stay functionally in sync:

- Every Android change must be mirrored to iOS, and every iOS change must be mirrored to Android — except platform-specific cases (OS APIs, platform UI/UX conventions, store requirements).
- If the mirror is not implemented immediately, a dedicated Linear issue for the other platform must already exist or be created before the PR merges.
