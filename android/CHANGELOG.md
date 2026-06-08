# Android Changelog

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
