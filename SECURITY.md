# Security policy

## Report a vulnerability

Do not open a public issue, discussion, or pull request for a security problem. This repository is public.

Use GitHub private vulnerability reporting:

1. Open the [Security tab](https://github.com/syrax90/mobaide/security) of this repository.
2. Select **Report a vulnerability**, or open the [report form](https://github.com/syrax90/mobaide/security/advisories/new) directly.

Only the maintainers can read the report.

If you cannot use GitHub, write to [support@mobaide.com](mailto:support@mobaide.com) and ask for a private channel. Do not put the details in the first message.

## What to include

- The component: Android app, iOS app, web client, agent, relay server, or install scripts.
- The version of that component.
- The steps that show the problem, and what an attacker gains.

Test only with your own account and your own computer. A local stack is the safest place: see [docs/manual-e2e-testing.md](docs/manual-e2e-testing.md). Do not read or change the data of other users.

## What happens next

- The maintainers read every report and answer in the private report.
- A fix is released before the details become public.
- You are named in the release notes if you want that.

## Supported versions

Security fixes go into the newest release of each component. The agent updates itself. The apps update through the App Store and Google Play. The web client and the relay server are always the newest version.
