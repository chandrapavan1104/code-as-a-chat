# Project picker loading and explicit chat navigation

The native catalog change made the context sheet publish projects only after
projects, CLI sessions and model discovery all completed. A cold catalog could
take 25 seconds; while waiting, the UI incorrectly showed an empty-project
message. A session/model error could also discard a successful projects read.
Model refresh repeated all three reads and disabled the project controls.

Projects, sessions and catalogs now load independently. Projects are placed
first in the sheet. Loading and project-load failure have distinct states and a
project-specific retry action. Refreshing models affects only the catalog and
never disables project selection or overwrites the selected directory.

`GET /api/model?include_catalog=false` returns the selected engine and saved
settings without CLI discovery. Chat startup uses this lightweight response and
keeps a successful project read even if model settings fail. After explicit
project selection the chat loads the destination's history, refreshes that
project's engine label, and dismisses the picker. Switch failures are visible.

The deployed app supports both independent conversations and legacy project
chats. Explicit selection in the project picker opens that project's chat and
persists it for reopening. The previous independent conversation remains in
history; automatic execution-context changes still preserve independent chats.
Drafts stay with the outgoing chat; reply/photo composer state is cleared when
opening a different project chat.

Regression widgets cover delayed catalogs/sessions, project failures/retry,
switch failure/retry, awaited navigation, selecting the current project, and
history replacement plus reopening. Backend regression verifies that lightweight
model settings never call catalog discovery.

Validation: 351 server and 179 Flutter tests passed on the active deployment.
Backend restart was clean; health passed. Lightweight settings returned in
0.02 seconds; the authenticated projects API returned all 33 projects. Signed
APK build 1791866981 is published. Physical-phone acceptance remains unverified.

## Follow-up: switches made outside the chat picker

Two paths still showed the wrong chat. Library → Projects switched only the
server, so the Chats tab (a long-lived screen) kept the previous thread. And on
resume the chat compared the server default with the open thread; the agent's
mid-turn `projects switch` never moves that default, so a chat that had followed
it was pulled back to the old project, and the followed thread was not saved.

`requestProjectChat` (chat_controller.dart) saves the project chat as selected
and broadcasts it; every open Gajala chat follows. Followed agent switches are
persisted, and resume follows only a change in the server default since the
screen last saw it (an explicit switch on the Mac or Telegram).
