# Changelog

## [1.1.4](https://github.com/syrax90/mobaide/compare/ios-v1.1.3...ios-v1.1.4) (2026-11-13)


### Features

* **agent,server,android,ios,web:** support GitHub Copilot CLI as a managed agent ([#870](https://github.com/syrax90/mobaide/issues/870)) ([bc37af4](https://github.com/syrax90/mobaide/commit/bc37af4e994114a10ce981338e208af92f94d5b9))
* **agent,server,android,ios,web:** support OpenCode sessions in the chat and the terminal ([#868](https://github.com/syrax90/mobaide/issues/868)) ([8ddd476](https://github.com/syrax90/mobaide/commit/8ddd4766010575519f134a8ed94457ce67776609))


### Bug Fixes

* **agent,android,ios,web:** stop showing Ready while a Claude Code session still runs ([#875](https://github.com/syrax90/mobaide/issues/875)) ([26b20ec](https://github.com/syrax90/mobaide/commit/26b20ec30d327c9b7e1f3b4b44eca5b22c96448d))
* **android,ios:** hide the keyboard on a tap on the chat timeline ([#876](https://github.com/syrax90/mobaide/issues/876)) ([8d47b01](https://github.com/syrax90/mobaide/commit/8d47b01bfac8f4236d819b7f909125c6eaadbcd8))
* **android,ios:** make Session info values copyable and confirm each copy ([#877](https://github.com/syrax90/mobaide/issues/877)) ([d22e245](https://github.com/syrax90/mobaide/commit/d22e245368df822e7579faf21d1aceb0f698deab))
* **ios:** keep the sign-in code field visible above the keyboard ([#872](https://github.com/syrax90/mobaide/issues/872)) ([e4841b1](https://github.com/syrax90/mobaide/commit/e4841b14cec0046ef6f225e3185bdf79520740c8))

## [1.1.3](https://github.com/syrax90/mobaide/compare/ios-v1.1.2...ios-v1.1.3) (2026-11-11)


### Features

* **agent,android,ios,web:** sign in to Claude Code, Codex and Antigravity from the chat ([#866](https://github.com/syrax90/mobaide/issues/866)) ([8d5dec9](https://github.com/syrax90/mobaide/commit/8d5dec93dfc6f48245d0daf93328195ec360152f))

## [1.1.2](https://github.com/syrax90/mobaide/compare/ios-v1.1.1...ios-v1.1.2) (2026-11-11)


### Features

* **agent,android,ios,web:** show which sessions are working and fix missing Ready alerts ([#862](https://github.com/syrax90/mobaide/issues/862)) ([a000817](https://github.com/syrax90/mobaide/commit/a0008176762dd06595f03bacd92936670b32dc09))
* **agent,server,android,ios,web:** notifications for Codex, Antigravity, Terminal sessions and failed turns ([#858](https://github.com/syrax90/mobaide/issues/858)) ([f22e0be](https://github.com/syrax90/mobaide/commit/f22e0bee88f2d989f962bb36c41f0f55dc9f8831))
* **ios:** show the context-use percent next to the chat usage bar ([#863](https://github.com/syrax90/mobaide/issues/863)) ([db3b4c0](https://github.com/syrax90/mobaide/commit/db3b4c076712d8ff9fb9c8c07c943f5854e66e27))


### Bug Fixes

* **agent,web,android,ios:** keep older messages in long ACP chats ([#859](https://github.com/syrax90/mobaide/issues/859)) ([fa672bb](https://github.com/syrax90/mobaide/commit/fa672bb16c20ee2848cce290e322fa88e9a07a41))

## [1.1.1](https://github.com/syrax90/mobaide/compare/ios-v1.1.0...ios-v1.1.1) (2026-11-10)


### Features

* **android,ios,web:** remove the external-sessions notice ([#855](https://github.com/syrax90/mobaide/issues/855)) ([d947831](https://github.com/syrax90/mobaide/commit/d94783134bfec76bb5ee32ffd0c7a7418d4d8911))

## [1.1.0](https://github.com/syrax90/mobaide/compare/ios-v1.0.20...ios-v1.1.0) (2026-11-10)


### Features

* **agent,web,android,ios:** ACP chat sessions for Claude Code and Antigravity ([#849](https://github.com/syrax90/mobaide/issues/849)) ([535ada1](https://github.com/syrax90/mobaide/commit/535ada1555cd5e187122a27acb9038327b46d98a))


### Miscellaneous Chores

* **ios:** cut the next iOS release as 1.1.0 ([#854](https://github.com/syrax90/mobaide/issues/854)) ([8a45b6d](https://github.com/syrax90/mobaide/commit/8a45b6d00898e2a8daa1b94917ecceaabec8b4c6))

## [1.0.20](https://github.com/syrax90/mobaide/compare/ios-v1.0.19...ios-v1.0.20) (2026-11-09)


### Features

* choose the base branch of a new session's worktree ([#845](https://github.com/syrax90/mobaide/issues/845)) ([ac39a0a](https://github.com/syrax90/mobaide/commit/ac39a0a38e4457945adb7e4a524a540e5abbf11a))

## [1.0.19](https://github.com/syrax90/mobaide/compare/ios-v1.0.18...ios-v1.0.19) (2026-11-08)


### Features

* **agent,web,android,ios,server:** AI cleanup of dictated text ([#835](https://github.com/syrax90/mobaide/issues/835)) ([596856d](https://github.com/syrax90/mobaide/commit/596856d50924c51145047a58f6e2f97e3d3a2306))
* **git:** allow checkout while sessions use the project folder ([#828](https://github.com/syrax90/mobaide/issues/828)) ([b9311a6](https://github.com/syrax90/mobaide/commit/b9311a6d8bd1eb266856f4b5999b0809fc2b77a1))

## [1.0.18](https://github.com/syrax90/claude_mobile/compare/ios-v1.0.17...ios-v1.0.18) (2026-11-05)


### Bug Fixes

* **server:** throttle password sign-in and require 8+ char passwords ([c40f660](https://github.com/syrax90/claude_mobile/commit/c40f6604595d8063a1b13da2a9410ceac3941c59))
* **server:** throttle password sign-in and require 8+ char passwords ([03773e0](https://github.com/syrax90/claude_mobile/commit/03773e0b42f4ad7044fdcc278efec169682f4268))
* **server:** validate register email and rate limit verification mail ([4f79a5c](https://github.com/syrax90/claude_mobile/commit/4f79a5c5f152c3eea19c63ce11d207ea303c938b))
* **server:** validate register email and rate limit verification mail ([b9eee0b](https://github.com/syrax90/claude_mobile/commit/b9eee0b41298a8359e7f07978ba21eb5ef02af4d))
