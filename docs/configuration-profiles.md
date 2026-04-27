# Configuration Profiles

One PowerShell command to swap every environment-specific file in the repo between named profiles (e.g. `local`, `mobaide`).

## Why

The repo has several files that differ by environment and are gitignored (since they carry secrets or machine-specific paths):

- `claude_agent/.env` — agent secrets, Linear projects, relay URL, ...
- `android/local.properties` — two build-time keys (`CENTRAL_SERVER_URL`, `GOOGLE_WEB_CLIENT_ID`) used by `android/app/build.gradle.kts`
- `android/app/src/main/res/xml/network_security_config.xml` — referenced by `AndroidManifest.xml`; must always exist
- `android/app/src/main/res/raw/caddy_root.crt` — trusted CA for the local Caddy stack

Manually editing each of them to switch between "local test stack (`start-all.ps1`)" and "production (mobaide.com)" is error-prone. This directory bundles the variants and swaps them in one command.

## Layout

```
config/
├── use-profile.ps1              # switcher script
├── README.md                    # you are here
├── .active                      # last-applied profile (gitignored, auto-written)
└── profiles/
    ├── _template/               # committed reference (safe defaults, no secrets)
    └── <your-profile>/          # gitignored, per-machine
        ├── profile.json
        ├── claude_agent.env
        ├── android.local.properties
        └── android-tls/
            ├── network_security_config.xml            # always required
            └── caddy_root.crt                         # only if xml references @raw/caddy_root
```

## Quick start

### 1. Create a profile from the template

```powershell
Copy-Item -Recurse config/profiles/_template config/profiles/local
```

Then edit the files:

- `config/profiles/local/profile.json` — set `description` to something useful (shown by `-List`).
- `config/profiles/local/claude_agent.env` — fill in `JWT_SECRET_KEY`, `PROJECTS`, relay settings, etc.
- `config/profiles/local/android.local.properties` — set `CENTRAL_SERVER_URL` (e.g. `https://test.mobaide.com`) and `GOOGLE_WEB_CLIENT_ID`.
- `config/profiles/local/android-tls/network_security_config.xml` — for a local Caddy stack, swap the `<trust-anchors>` block to include `<certificates src="@raw/caddy_root"/>` (see the comments in the template file) and drop `caddy_root.crt` beside it.

### 2. Apply it

```powershell
./config/use-profile.ps1 local
```

The script reports each operation (`create`, `overwrite`, `merge`, `delete`, `skip`) and asks for confirmation before writing. Use `-Force` to skip the prompt, `-DryRun` to preview without changing anything.

### 3. Inspect and list

```powershell
./config/use-profile.ps1 -List    # all profiles with descriptions + currently active
./config/use-profile.ps1 -Show    # currently active profile only
```

## Operation modes

| Target                                                              | Mode           | Behavior                                                                                  |
|---------------------------------------------------------------------|----------------|-------------------------------------------------------------------------------------------|
| `claude_agent/.env`                                                 | overwrite      | Replaced verbatim from `claude_agent.env`.                                                |
| `android/local.properties`                                          | **merge**      | Only `CENTRAL_SERVER_URL` and `GOOGLE_WEB_CLIENT_ID` are updated. `sdk.dir` is preserved. |
| `android/app/src/main/res/xml/network_security_config.xml`          | overwrite      | Replaced from profile. Required by `AndroidManifest.xml` — must exist in every profile.   |
| `android/app/src/main/res/raw/caddy_root.crt`                       | copy-or-delete | Copied if present in profile's `android-tls/`; **deleted** from target if absent.         |

Files missing from the profile folder are simply skipped (except `caddy_root.crt`, which is actively deleted — otherwise a stale cert could survive a switch to a profile that doesn't need it).

## Example: `local` vs `mobaide`

| Aspect                          | `local`                                                       | `mobaide`                         |
|---------------------------------|---------------------------------------------------------------|-----------------------------------|
| `CENTRAL_SERVER_URL`            | `https://test.mobaide.com`                              | `https://mobaide.com`             |
| `android-tls/caddy_root.crt`    | present (your Caddy root)                                     | absent                            |
| `network_security_config.xml`   | includes `<certificates src="@raw/caddy_root"/>`              | system trust only                 |
| `claude_agent.env` `RELAY_*`    | pointed at local central server                               | pointed at mobaide.com            |

After `./config/use-profile.ps1 local`, running `./start-all.ps1` and rebuilding the Android app will wire everything to the local stack. Switching with `./config/use-profile.ps1 mobaide` and rebuilding points the app at production.

## Notes

- The `_template` profile cannot be applied directly — the switcher refuses it to prevent accidental clobbers with placeholder values. Copy it first.
- Profile folders under `config/profiles/` (except `_template`) are gitignored; they contain secrets.
- The switcher does not restart running processes. After applying a profile, re-run `start-all.ps1` (agent) and rebuild the Android app to pick up the new config.
- If you edit a managed file by hand (say, `claude_agent/.env`) after applying a profile, the next `use-profile.ps1` run will overwrite your edits. Put persistent changes into the profile folder, not the canonical target.
