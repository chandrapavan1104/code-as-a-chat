# Gogon home integration

Gajala's `home` skill and Library → Home use the existing local Gogon API at
`http://127.0.0.1:8500`. `GOGON_HOME_URL` may override the loopback service address.
Only Gogon's server owns the panel login; Gajala never reads the panel PIN,
starts another panel session, or uses the Firebase roommate bridge.

Use ordinary chat or voice requests, for example:

- “Turn Br3 Light on.”
- “Turn off the lights in the master bedroom.”
- “Dim Br3 Light to 40 percent.”
- “Set Level 2 cooling to 74 degrees.”
- “Turn the porch light off every day at 10 PM.”
- “What lights did I leave on?”
- “Open the garage.”

The skill first retrieves actual room/device IDs and asks when names are
ambiguous. Home schedules execute in Gogon in the Mac's local timezone; these
are physical lighting schedules, not Gajala reminder notifications. Existing
lighting rules can be enabled/disabled. Alarm arm/disarm, sensor bypass and door
locks are excluded. The panel currently has no enrolled intrusion sensors, so
Ready/connected does not mean the house is protected.

Garage movement always requires a fresh on-phone confirmation that the owner is
at the door and can watch it. The Home screen uses a one-use 30-second ticket;
chat/voice uses the authenticated phone-request bridge. Background jobs and plain
non-app turns cannot approve it. Unknown, stale or moving garage state blocks a
new movement. Acknowledgement alone is not completion: Gajala polls for up to
25 seconds for a new sensor report and otherwise reports the position unverified.
It does not automatically repeat the command.

Gogon exposes observation timestamps separate from optimistic acknowledgement
states. Gajala confirms switches only from actual post-command reports and
thermostat fields only from reports newer than the acknowledged command. Stale
panel state blocks commands. Unreachable switches, partial room changes and
uncertain transport outcomes stay visible; multi-device work is bounded.

Validation: 340 server tests, 172 Flutter tests and 3 simulated Z-Wave observation
tests pass. The Home screen analyzes cleanly. Live GETs verified 48 switches,
42 with confirmed reads, two thermostats and one garage without sending any
physical test command. Physical-phone and hardware action acceptance remain:
try one named light, then its dimmer, and a thermostat setting you actually want.
Test garage control only while at the door. Keep the app open for confirmation.
