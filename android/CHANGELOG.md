# Android Changelog

## [2.0.0](https://github.com/syrax90/claude_mobile/compare/android-v1.15.0...android-v2.0.0) (2026-06-26)


### ⚠ BREAKING CHANGES

* **sessions:** wire protocol bumped 5→6. The claude_options envelope field changed shape: dropped max_effort (bool); added model (str), effort (str|null), fast_mode (bool), ultracode (bool), append_system_prompt (str|null). Any relay or client built against proto 5 will fail the version handshake with a proto 6 agent. New sessions default to effort="high" instead of always "max".

### Features

* **sessions:** replace Claude max-effort toggle with model + effort controls (CLA-84) ([3b52a46](https://github.com/syrax90/claude_mobile/commit/3b52a46439d066fc15c95fbd8084d84b79bf195e))


### Bug Fixes

* **android:** Replace the boolean Max effort toggle with rich Claude session controls (CLA-84) ([3b052b1](https://github.com/syrax90/claude_mobile/commit/3b052b1cda8438c98b9375128ac378612c97f46c))

## [1.15.0](https://github.com/syrax90/claude_mobile/compare/android-v1.14.0...android-v1.15.0) (2026-06-25)


### Features

* **agent+android:** gray out Claude/Codex mode buttons when CLI is not installed ([7134c33](https://github.com/syrax90/claude_mobile/commit/7134c33f1480c8e33be6d4c0b0c172380a09096c))
* **android:** gray out Claude/Codex mode buttons when CLI is not installed ([cf95d70](https://github.com/syrax90/claude_mobile/commit/cf95d708274f048bdb9f5dc47e27b36563e6ddfb))

## [1.14.0](https://github.com/syrax90/claude_mobile/compare/android-v1.13.0...android-v1.14.0) (2026-06-24)


### Features

* add Codex to the create-session wizard (CLA-69) ([f41919c](https://github.com/syrax90/claude_mobile/commit/f41919c1c51f5191cf754c80d9e2471e108a04f3))
* add Codex to the create-session wizard (CLA-69) ([c7600f6](https://github.com/syrax90/claude_mobile/commit/c7600f6df2a019fec6a37f73ba9376a932a2eaf4))
* **agent:** preserve per-project Auto Scan initial prompt across sessions ([a58be75](https://github.com/syrax90/claude_mobile/commit/a58be75a100c0e0e276f02451b1b1c6a777af114))
* **agent:** preserve per-project Auto Scan initial prompt across sessions ([dc95191](https://github.com/syrax90/claude_mobile/commit/dc9519167d024895b2bbaf90a42126db5ba44e70))
* **android:** make terminal control-key bar CLI-aware for Codex (CLA-70) ([c0f50ac](https://github.com/syrax90/claude_mobile/commit/c0f50ac10cb8bcf15d7f3ca4f02473d46d56cd68))
* **android:** make terminal control-key bar CLI-aware for Codex (CLA-70) ([4abfc10](https://github.com/syrax90/claude_mobile/commit/4abfc1005ad3cda9a418baba406009e5696016f8))
* **codex:** add in-app Codex CLI authentication/login flow (CLA-71) ([63b72b6](https://github.com/syrax90/claude_mobile/commit/63b72b6dd6d1dfb24f81f057b3de95778de05e0e))
* **codex:** add in-app Codex CLI authentication/login flow (CLA-71) ([6f7fb00](https://github.com/syrax90/claude_mobile/commit/6f7fb00953d89d44e9cad2ed17e29e066185b599))


### Bug Fixes

* **agent:** address Codex review — harden auto_scan_prompt persistence and seeding ([456a5f8](https://github.com/syrax90/claude_mobile/commit/456a5f874779dabed3391306ed16ba2703bc8dee))
* **android:** seed Codex sessionKind from REST on cold launch (CLA-70 review) ([88012f8](https://github.com/syrax90/claude_mobile/commit/88012f8b96ea3b9371206aca15b02b71de1b5ca8))
* **codex:** address PR [#270](https://github.com/syrax90/claude_mobile/issues/270) review-round-2 — launcher test CI fix + SecretTextField IME hardening (CLA-71) ([1348d83](https://github.com/syrax90/claude_mobile/commit/1348d8386e92ac0ef311e4ee69bfef5fd57d6db3))
* **codex:** remap auth-required codes to 409/400 to avoid AuthInterceptor retry (CLA-71) ([62d38c4](https://github.com/syrax90/claude_mobile/commit/62d38c4ff5cd96adf6f60afd5c184e2d0366170a))

## [1.13.0](https://github.com/syrax90/claude_mobile/compare/android-v1.12.0...android-v1.13.0) (2026-06-23)


### Features

* **agent:** generalize CLI launch abstraction and add Codex CLI support (CLA-68) ([de719f2](https://github.com/syrax90/claude_mobile/commit/de719f27d013f277ede0bac900ca4ff8bd4fb59d))
* **agent:** generalize CLI launch abstraction and add Codex CLI support (CLA-68) ([f9c0d83](https://github.com/syrax90/claude_mobile/commit/f9c0d8375d5a5106cb78561881b9cb93b8c584b3))

## [1.12.0](https://github.com/syrax90/claude_mobile/compare/android-v1.11.0...android-v1.12.0) (2026-06-17)


### Features

* **agent:** surface macOS file-access (TCC) freezes to the app instead of hanging ([24740f0](https://github.com/syrax90/claude_mobile/commit/24740f0b212e20616ab516f8a18c661f2079cab5))
* **agent:** surface macOS file-access (TCC) freezes to the app instead of hanging ([ce03474](https://github.com/syrax90/claude_mobile/commit/ce034747322213b587cdbb41603f849b2baa2d8f))
* **terminal:** default Terminal Display to Static mode ([625da1b](https://github.com/syrax90/claude_mobile/commit/625da1b40b497205732c3fcbb27f93b99c405c44))
* **terminal:** default Terminal Display to Static mode ([711034d](https://github.com/syrax90/claude_mobile/commit/711034d770dbb660b11ec0ef1df0630823c4c223))

## [1.11.0](https://github.com/syrax90/claude_mobile/compare/android-v1.10.1...android-v1.11.0) (2026-06-16)


### Features

* **android:** guide users to apply Agent ID + Token on the agent host ([09677a4](https://github.com/syrax90/claude_mobile/commit/09677a4d5b82fb170b82e77be379ba34e24d5828))
* **android:** guide users to apply Agent ID + Token on the agent host ([84563d6](https://github.com/syrax90/claude_mobile/commit/84563d607e3a7864f67064752b2c35c918442eb4))

## [1.10.1](https://github.com/syrax90/claude_mobile/compare/android-v1.10.0...android-v1.10.1) (2026-06-13)


### Bug Fixes

* **agent:** allow force-deleting worktrees with modified/untracked files ([9ab4892](https://github.com/syrax90/claude_mobile/commit/9ab4892a1ff1b8245470f39303d6b88cbe812952))
* **agent:** fix Send button not executing commands without manual Enter ([086699f](https://github.com/syrax90/claude_mobile/commit/086699fd3276f2be494f4a32f8df6820c48c5bc4))
* **git:** allow force-deleting worktrees with modified/untracked files ([003fb66](https://github.com/syrax90/claude_mobile/commit/003fb6673efe7f8b0fe77ac37c19473bf1418d74))
* **terminal:** fix Send button not executing commands without manual Enter ([261fd96](https://github.com/syrax90/claude_mobile/commit/261fd961bf5dbd5b36c2477c72b3016c132e463c))

## [1.10.0](https://github.com/syrax90/claude_mobile/compare/android-v1.9.0...android-v1.10.0) (2026-06-09)


### Features

* **agent:** add Terminal session worktree config page ([755a94b](https://github.com/syrax90/claude_mobile/commit/755a94bc314162c358159a5b29cf7bd0269c5f87))
* **sessions:** add Terminal session worktree config page ([0a65eee](https://github.com/syrax90/claude_mobile/commit/0a65eee9343c8a52dc066ba337acd30001c9d253))

## [1.9.0](https://github.com/syrax90/claude_mobile/compare/android-v1.8.2...android-v1.9.0) (2026-06-09)


### Features

* **agent:** pop-up notifications when a Claude session awaits input or ends(CLA-64) ([6410cdb](https://github.com/syrax90/claude_mobile/commit/6410cdb50d471709d1aa560a62b8040e516d1d40))
* pop-up notifications when a Claude session awaits input or ends (CLA-64) ([848af44](https://github.com/syrax90/claude_mobile/commit/848af446a8cb6db7c699c34bbfa0a8093ede7271))

## [1.8.2](https://github.com/syrax90/claude_mobile/compare/android-v1.8.1...android-v1.8.2) (2026-06-09)


### Bug Fixes

* **android:** make Settings screen scrollable so the Logout button isn't clipped ([3aed327](https://github.com/syrax90/claude_mobile/commit/3aed327a65d38d6fdf693fd311524ee3fabd6737))
* **android:** remove agent id from connection label ([15c08f5](https://github.com/syrax90/claude_mobile/commit/15c08f52217f49e0d687714efc2370f301edfbb1))
* **android:** source app version from BuildConfig instead of hardcoded v1.0.0 ([8ef3f5e](https://github.com/syrax90/claude_mobile/commit/8ef3f5e80a1978e886ad21f106d23ca1d49735aa))
* **settings:** make Settings screen scrollable so the Logout button isn't clipped ([947d020](https://github.com/syrax90/claude_mobile/commit/947d020ab1de4895ebc84dfe39acd9e9418343e8))
* **settings:** remove agent id from connection label ([84e005d](https://github.com/syrax90/claude_mobile/commit/84e005d3a857c44c4d1fdc22c9aa91bb2b8b999e))
* **settings:** source app version from BuildConfig instead of hardcoded v1.0.0 ([0030184](https://github.com/syrax90/claude_mobile/commit/00301845d8b6cb00901b0ba4f2ee174d7fd01685))

## [1.8.1](https://github.com/syrax90/claude_mobile/compare/android-v1.8.0...android-v1.8.1) (2026-06-08)


### Bug Fixes

* **terminal:** prepend Ctrl+A/Ctrl+K line clear before every composer send ([8739eb4](https://github.com/syrax90/claude_mobile/commit/8739eb41f80ac5003e217920e67607fa452a11c0))

## [1.8.0](https://github.com/syrax90/claude_mobile/compare/android-v1.7.1...android-v1.8.0) (2026-05-29)


### Features

* **android:** add Mobaide adaptive launcher icon ([3030f87](https://github.com/syrax90/claude_mobile/commit/3030f87e8a1456819be8cfa285685bb9875f464c))


### Bug Fixes

* **android:** add Mobaide adaptive launcher icon ([441f0bc](https://github.com/syrax90/claude_mobile/commit/441f0bc5148068e125d8bac99e7eeea2afea3811))
* **android:** remove Paste and More from long-press selection toolbar ([6f9208e](https://github.com/syrax90/claude_mobile/commit/6f9208e04389f971f7f2119987da022dba064ff8))
* **terminal:** remove Paste and More from long-press selection toolbar ([979f0a9](https://github.com/syrax90/claude_mobile/commit/979f0a994ea241107593a8402a9b4014f81d794d))

## [1.7.1](https://github.com/syrax90/claude_mobile/compare/android-v1.7.0...android-v1.7.1) (2026-05-29)


### Bug Fixes

* **android:** add long-press Send-to-Input action and fix silent Copy no-op (CLA-63) ([299a7e8](https://github.com/syrax90/claude_mobile/commit/299a7e8c78e4dbb5b14530f7633dcf84eec8a5f4))
* **android:** make TerminalView focusable so long-press selection toolbar appears (CLA-63) ([79384b6](https://github.com/syrax90/claude_mobile/commit/79384b6fc3c50e9934453e6705fcfe24fea37d25))
* **android:** remove dead foreground service and its artifacts ([1475656](https://github.com/syrax90/claude_mobile/commit/1475656689a68bdc942fd8f2a3feabd3fd4f2052))
* **android:** remove dead foreground service and its artifacts ([d6d98b3](https://github.com/syrax90/claude_mobile/commit/d6d98b3677f74edfc6ad3fb27216c1e8aecedf85))
* **terminal:** make TerminalView focusable so long-press selection toolbar appears (CLA-63) ([1c61af7](https://github.com/syrax90/claude_mobile/commit/1c61af735e7b20d072ab404f1abb98a542d483e6))

## [1.7.0](https://github.com/syrax90/claude_mobile/compare/android-v1.6.0...android-v1.7.0) (2026-05-25)


### Features

* **auth:** replace hardcoded OTP placeholder with segmented CodeInputField ([d351ac3](https://github.com/syrax90/claude_mobile/commit/d351ac32488a09905ba6689d7ed25ebe1630355a))


### Bug Fixes

* **android:** update network security config to disallow cleartext traffic ([b5cc408](https://github.com/syrax90/claude_mobile/commit/b5cc40896f61cb8ba0815e9a963a6498e026289f))
* **android:** update network security config to disallow cleartext traffic ([6a7e1a1](https://github.com/syrax90/claude_mobile/commit/6a7e1a1630958a56dbd08b85cfdc75f6d321db65))

## [1.6.0](https://github.com/syrax90/claude_mobile/compare/android-v1.5.2...android-v1.6.0) (2026-05-23)


### Features

* **ui:** rename "Your devices" to "My devices" and add Google logo to sign-in buttons ([657fd92](https://github.com/syrax90/claude_mobile/commit/657fd924ddf2d6e5bc8d39a3b70775922d98a086))


### Bug Fixes

* **android:** rename "Your devices" to "My devices" and add Google logo to sign-in buttons ([bbc4eab](https://github.com/syrax90/claude_mobile/commit/bbc4eab6c4617c47b35e5729b093eab55a200905))
* **android:** replace 256 KB single-frame replay with 4 MB chunked ring buffer (proto v3) ([b1740e9](https://github.com/syrax90/claude_mobile/commit/b1740e92bbe3fcc77edcf620490ae9000985217b))
* **replay:** replace 256 KB single-frame replay with 4 MB chunked ring buffer (proto v3) ([bd8bb63](https://github.com/syrax90/claude_mobile/commit/bd8bb633f83e7a6552b0db15f00291b71ec53504))

## [1.5.2](https://github.com/syrax90/claude_mobile/compare/android-v1.5.1...android-v1.5.2) (2026-05-23)


### Bug Fixes

* **android:** force-reconnect on screen-on + clear emulator before server replay to unstick "Reconnecting" overlay ([c1aea71](https://github.com/syrax90/claude_mobile/commit/c1aea7117000373a5179677d4176c5156a5712b9))
* **terminal:** force-reconnect on screen-on + clear emulator before server replay to unstick "Reconnecting" overlay ([51ebff2](https://github.com/syrax90/claude_mobile/commit/51ebff2ef958b03aca9adc8a007437a7a2e942a4))

## [1.5.1](https://github.com/syrax90/claude_mobile/compare/android-v1.5.0...android-v1.5.1) (2026-05-23)


### Bug Fixes

* **android:** respect lp.width in TerminalView.onMeasure + direction-aware HSV intercept to restore horizontal pan in Static display mode ([9162b23](https://github.com/syrax90/claude_mobile/commit/9162b23d66e70f858ddc607c3ed9d3da21d0e992))
* **terminal:** respect lp.width in TerminalView.onMeasure + direction-aware HSV intercept to restore horizontal pan in Static display mode ([4e2a6c6](https://github.com/syrax90/claude_mobile/commit/4e2a6c66176082fde34deb575b3f55a5f4574e06))

## [1.5.0](https://github.com/syrax90/claude_mobile/compare/android-v1.4.0...android-v1.5.0) (2026-05-22)


### Features

* self-host privacy policy at /privacy.html and surface links in Android UI ([9eb0b90](https://github.com/syrax90/claude_mobile/commit/9eb0b90c184b13617810b52d08faa9c25b6834dd))
* self-host privacy policy at /privacy.html and surface links in Android UI ([882251b](https://github.com/syrax90/claude_mobile/commit/882251b75e5f32777dfa356ae8906f7f48ed18ca))


### Bug Fixes

* **android:** replace Compose horizontalScroll with native HorizontalScrollView for terminal gesture handling ([4d5168d](https://github.com/syrax90/claude_mobile/commit/4d5168d3c977fe4690b42fe9d6e5a0a3f9492b28))
* **terminal:** replace Compose horizontalScroll with native HorizontalScrollView for terminal gesture handling ([b7edd46](https://github.com/syrax90/claude_mobile/commit/b7edd46b239c8c632949b426937b508772332846))

## [1.4.0](https://github.com/syrax90/claude_mobile/compare/android-v1.3.0...android-v1.4.0) (2026-05-22)


### Features

* **account-deletion:** user self-service deletion + portal + 14-day grace (CLA-61) ([8fb14dd](https://github.com/syrax90/claude_mobile/commit/8fb14ddb3d18c83dd2c9cae4451ddabeda4bcee1))
* **android:** user self-service deletion + portal + 14-day grace (CLA-61) ([c8ea9cb](https://github.com/syrax90/claude_mobile/commit/c8ea9cb97c4dcf86dfc3967be84611230dfd4c90))
* **devices:** per-install device UUID + user-scoped devices API + "Your devices" screen (CLA-62) ([354fb68](https://github.com/syrax90/claude_mobile/commit/354fb6890ed16de899a1952db81f6fce8d630891))
* **devices:** per-install device UUID + user-scoped devices API + "Your devices" screen (CLA-62) ([60bc57a](https://github.com/syrax90/claude_mobile/commit/60bc57a38aa91f5be0aa14143c70a300205ec93e))


### Bug Fixes

* **account-deletion:** resolve 5 reported issues end-to-end ([0775b9d](https://github.com/syrax90/claude_mobile/commit/0775b9d544ea1165d1d4c0c3523c841faf87ba7e))
* **android:** resolve 5 reported issues end-to-end ([1a43c9b](https://github.com/syrax90/claude_mobile/commit/1a43c9becff506141f30eef1fac930a512b0bba7))

## [1.3.0](https://github.com/syrax90/claude_mobile/compare/android-v1.2.0...android-v1.3.0) (2026-05-21)


### Features

* **agent:** add Custom Path option to project picker for ad-hoc sessions (CLA-60) ([6ec5287](https://github.com/syrax90/claude_mobile/commit/6ec52878013b2ba1790cec88dc4386c24e6a3631))
* **sessions:** add Custom Path option to project picker for ad-hoc sessions (CLA-60) ([72cb848](https://github.com/syrax90/claude_mobile/commit/72cb848a9552f667d3cf696b58269b2d61bcb0e0))

## [1.2.0](https://github.com/syrax90/claude_mobile/compare/android-v1.1.0...android-v1.2.0) (2026-05-20)


### Features

* **agent:** add Terminal session mode as third create-session tile (CLA-59) ([874171a](https://github.com/syrax90/claude_mobile/commit/874171a4078ecb9e67455af0ace9f46c81847197))
* **sessions:** add Terminal session mode as third create-session tile (CLA-59) ([5d0b61e](https://github.com/syrax90/claude_mobile/commit/5d0b61e49d195874de8bf6d28b02d0757757fedc))
* **terminal:** refactor InputBar for orientation-aware layout and tidy control-key panel ([1e81e00](https://github.com/syrax90/claude_mobile/commit/1e81e00701c6ca560b4f2e830054d24a07bf489c))
* **terminal:** refactor InputBar for orientation-aware layout and tidy control-key panel ([73ed6f2](https://github.com/syrax90/claude_mobile/commit/73ed6f2c03b9caf56e162e67f3a259f50ad82391))


### Bug Fixes

* **settings:** display user email in "Logged in as" and rename "Standard mode" to "Claude code session" ([666bf18](https://github.com/syrax90/claude_mobile/commit/666bf18e7fd65a7b3f7d670db9bfec73480513de))

## [1.1.0](https://github.com/syrax90/claude_mobile/compare/android-v1.0.3...android-v1.1.0) (2026-05-19)


### Features

* **sessions:** show Claude Code OSC terminal title on Android session list ([95f7ea7](https://github.com/syrax90/claude_mobile/commit/95f7ea738aa62e0151d8385c9d5490da7b61c863))


### Bug Fixes

* **agent:** show Claude Code OSC terminal title on Android session list ([969a878](https://github.com/syrax90/claude_mobile/commit/969a878ac2fba99176d3048ab17b61837ae4dcec))
* **infra:** restore release tag creation by deleting empty Releases ([67c45ba](https://github.com/syrax90/claude_mobile/commit/67c45ba6bf685c9d2ccd0d8ed66e71f89e34e25a))
* **infra:** restore release tag creation by deleting empty Releases ([81933be](https://github.com/syrax90/claude_mobile/commit/81933be753698a1a00bcb584f6a5d2bd21c11111))

## [1.0.3](https://github.com/syrax90/claude_mobile/compare/android-v1.0.2...android-v1.0.3) (2026-05-18)


### Bug Fixes

* **android:** setting GOOGLE_WEB_CLIENT_ID for release ([be04036](https://github.com/syrax90/claude_mobile/commit/be04036489d380c417035cc2e997f3c4bbaba351))
* **android:** setting GOOGLE_WEB_CLIENT_ID for release ([2e8d847](https://github.com/syrax90/claude_mobile/commit/2e8d8471c2d1c9a4ef4eba598974ad7b0c010cf2))

## [1.0.2] - 2026-05-18

### Features

* **agents:** add install-commands bottom sheet with empty-state guide and overflow recall
