# Social sign-in setup

Provider configuration for building the clients and running your own central server. Users of the hosted service at `mobaide.com` need none of this.

## Configuring Google Sign-In

The Android "Sign in with Google" button uses the Credential Manager API and requires an OAuth 2.0 **Web** client ID provisioned in Google Cloud Console:

1. In Google Cloud Console → **APIs & Services → Credentials**, create (or reuse) a project and add two OAuth 2.0 client IDs:
   - **Web application** — copy its client ID (format `NNN-xxxxx.apps.googleusercontent.com`).
     - This is the ID the Android app asks Credential Manager for AND the ID the server verifies ID tokens against.
     - The browser client uses it too. It fetches the id at runtime from `GET /api/version`, so it needs no build-time value.
     - Every origin the SPA is served from (`https://app.mobaide.com`, plus `http://localhost:5173` for `npm run dev`) must be listed under this client's **Authorized JavaScript origins**, or the web Sign in with Google button fails to render.
   - **Android application** — register package `com.mobaide.app` and your debug + release signing-certificate SHA-1 fingerprints. Google Play Services requires this before any credential is returned, even though the app only passes the Web client ID.
2. Put the Web client ID in your profile's `android.local.properties`:
   ```
   GOOGLE_WEB_CLIENT_ID=NNN-xxxxx.apps.googleusercontent.com
   ```
   The profile switcher merges this into `android/local.properties` (git-ignored) without touching `sdk.dir`. For CI builds, export `GOOGLE_WEB_CLIENT_ID` as an environment variable instead.
3. In the server's `.env`, set the SAME value:
   ```
   GOOGLE_CLIENT_ID=NNN-xxxxx.apps.googleusercontent.com
   ```
4. Rebuild the APK. If the client ID is missing or still the placeholder, the app surfaces a clear config error instead of silently pretending no Google account exists on the device.

The iOS app's sign-in setup (a separate **iOS application** OAuth client ID via the gitignored `ios/Config/Local.xcconfig`, plus Sign in with Apple, which requires `APPLE_CLIENT_ID` in the server's `.env`) is covered in the "Sign-in configuration" section of [docs/ios-client.md](ios-client.md).

## Configuring Sign in with Apple on the web

The web client offers Sign in with Apple so accounts created in the iOS app, which often have a private-relay address and no password, can sign in on a browser at all.

It needs one extra server value, `APPLE_WEB_CLIENT_ID`, holding an Apple **Services ID**. The client fetches it at runtime from `GET /api/version`, so there is no build-time value.

> [!WARNING]
> The Services ID must be created with `com.mobaide.app` as its **primary App ID**. That grouping is what makes Apple issue the same user identifier on web as in the app. Getting it wrong creates duplicate accounts rather than an error.

The full setup, including the domain verification each registered origin needs, is in ["Sign in with Apple on the web"](deployment.md#deploying-the-web-spa-appmobaidecom).
