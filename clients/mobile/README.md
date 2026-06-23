# Code-as-a-Chat — Mobile (Expo)

Native client for the Code-as-a-Chat orchestrator. Talks to the same
`/run` + `/skills` API the Telegram bot uses, guarded by the API token.

## Run it (Expo Go — no Android Studio needed)

1. Install **Expo Go** on your Android phone (Play Store).
2. On the Mac:
   ```sh
   cd clients/mobile
   npm start
   ```
3. Scan the QR with Expo Go.
4. In the app's Setup screen, enter:
   - **Server URL** — `https://<your-mac>.ts.net` (Tailscale) or
     `http://<mac-LAN-ip>:8000` (same Wi-Fi)
   - **API Token** — contents of `~/.codeasachat/api_token` on the Mac
5. Tap **Connect**. If it finds your skills, you're in.

## Architecture

```
App.js                 root — routes Setup ↔ Chat, loads saved config
src/storage.js         persists {serverUrl, token} + a stable session id
src/api.js             /health, /skills, /run  (sends X-API-Token)
src/screens/SetupScreen.js   connection config + test
src/screens/ChatScreen.js    chat with Gajala; /commands routed too
src/theme.js           dark palette
```

Free-text → `shell` (Gajala agent). Messages starting with `/` route to that
command directly (e.g. `/status`, `/notes`, `/diary recent`).

## Build an installable APK (later)

```sh
npx eas build -p android --profile preview
```

(Needs an Expo account; produces a downloadable `.apk`.)
