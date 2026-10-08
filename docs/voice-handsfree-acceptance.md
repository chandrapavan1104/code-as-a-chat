# Hands-free voice acceptance

Automated checks cover routing, confirmation cancellation, contact selection,
permission reconfirmation, per-turn handler isolation, and truthful music status.
They do not establish Android app compatibility or voice quality on the phone.

After installing the October 6 build:

1. Open voice settings. Preview an Indian English voice and save the preferred
   voice. Gender is shown only if Android reports it. Install en-IN voice data
   through Android TTS settings if none appears.
2. Say “call PSS”. Grant Contacts/Phone permissions if asked. Check that the
   correct contact name is spoken (without reciting the full number). Say “no”; no call should start.
3. Repeat with a consenting test contact and say “yes”. Android should place
   the call without a dial-button tap. A newly granted call permission must
   trigger another spoken confirmation. Ambiguous contacts need a spoken choice.
4. Close voice mode or background Gajala while confirmation is pending. No
   later reply may place that call. Silence and unclear answers must cancel.
5. Say “play Rockstar Hindi songs”. YouTube Music should receive a playback
   request, not a browser search. Enable optional playback verification in voice
   settings and check the actual track/album in YouTube Music. If the app or
   account cannot play, Gajala must say it could not verify playback.
6. After a call/music request, open the project chat. Verify the command,
   confirmation, your spoken answer, and result are present. Restart the app
   offline and confirm logs remain. Reconnect and confirm logs sync once without
   placing another call or issuing another playback command.
7. Ask an ordinary question, then ask another after the answer without tapping
   the microphone. Try flashlight/volume controls in the same conversation.
   Say “stop” to close voice mode.

YouTube Music playback remains dependent on its installed app, account, and
Android support. Notification access enables media-session verification; the
separate Phone abilities notification-reading switch remains independent.

## October 8 additions

- Enable Gajala YouTube Music control under Android Accessibility through voice
  settings. Try Rockstar, DSP, Thaman and Telugu songs. Check the selected result
  and audible playback; an unmatched page must stop with an honest status.
- Open Teach Hey Gajala and record the eight prompted clips in your normal voice.
  Samples stay in memory. Cancel/background during capture or evaluation; previous
  calibration must remain. Verify real wake detections and reset if false wakes rise.
- Ask a substantial sourced research question, leave the app, and return later.
  The research card should show progress and the final reply reference the original
  question once. Stop cancels the worker. A timeout or failure must leave a reply.
- Test Mac Lock and Wake manually when convenient. A denied Mac Accessibility
  shortcut must report display-sleep fallback rather than claim a verified lock.
  Wake opens the display; normal macOS authentication still applies.

## Conversation baseline acceptance

- After updating, General opens as an independent conversation. Start a second
  chat from New conversation, leave/reopen the app, and confirm selection persists.
- Long-press an older persisted message, choose Reply, and send a correction after
  discussing another topic. Check quoted source, tap-to-jump, and relevant response.
- Change execution project while remaining in the same new chat. Queued messages
  must retain the project selected when sent. Legacy project histories remain in
  the conversation picker.
- Open voice from that chat and make a local command. Its transcript and outcome
  must appear there. Resume voice through the assistant entry and check selected
  conversation continuity.
- Retry an offline reply after reconnecting; it must appear once and retain its
  reply target. A slow research result must link to its originating question.
- In voice settings, refresh the YouTube Music connection check, then request music.
  View last playback details if it fails. Record actual app version, stage/reason,
  selection, and audible playback. An advertised command alone is not a pass.
