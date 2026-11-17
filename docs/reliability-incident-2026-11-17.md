# Gajala reliability incident — November 17, 2026

## Evidence and scope

Investigated running release #78 (416b1fa4), not the older dirty owner checkout.
The fixes are in codex/gajala-reliability-debug; owner changes remain untouched.
No real home or trading action was executed during diagnosis.

## Chats

The Code-as-a-chat chat repeatedly fell from Claude to gpt-4o-mini after OAuth
failures. One Gemini attempt returned IneligibleTierError/UNSUPPORTED_CLIENT,
which the assistant incorrectly described as needing re-login. Typed /trade
commands entered ShellSkill instead of the explicit registered skill; the model
then treated them as natural language or coding questions. Fixed generic exact
slash-command dispatch at the orchestrator boundary, retaining the registered
skill and its authorization checks. No saved command is replayed automatically.

A failed review returned a resumed Claude session's JSON: current usage zero,
result duration 57ms, cumulative-looking API duration 8,254,843ms and total_cost_usd
140.3815. The tool itself took 1.939 seconds. A subsequent report claimed this
was a bill incurred by that review. The evidence does not establish a new bill.
Errors now expose result/message, not the raw telemetry; saved error payloads
are filtered at generation-context boundaries without rewriting saved history.
Successful report fragments survive partial-summary recovery instead of becoming
one-line, unhelpful Observed actions recaps.

## Claude login

Original CLI 2.1.299 reported loggedIn:true outside the coding sandbox, with a
readable credential item and a no-timeout login Keychain. A synthetic, tool-free
provider request nevertheless failed: OAuth session expired and could not be
refreshed. Local saved-login presence is not provider authentication health.
An initial sandboxed status falsely appeared signed out because credentials
were inaccessible there; unrestricted checks corrected that conclusion.

The same-provider backup model was previously tried on shared auth failure;
this cannot repair shared credentials. Such retries are now stopped. Native
/auth claude reads auth status without prompting a model; it preserves known
provider failures. /auth claude verify makes an explicit bounded tool-free
verification request and clears failure state only on actual success.

The precise reason for repeated refresh invalidation is not established.
Concurrent-client refresh bugs are reported upstream, but this machine's logs
do not prove that cause. Tokens are not read, exported, copied or periodically
rewritten. A provider-rejected refresh may require an owner-completed login.

## Mac and automation availability

Recent reboots: October 30, October 31, November 5 and November 17 at 08:40 local.
The November 17 ResetCounter records Boot failure count 1 and faults
uv,vdd_boost_uvlo rst sgpio target_off_restart. These names are consistent with
a power/reset fault; they do not prove which outlet, cable, supply or board is
responsible. Owner reports a direct wall outlet. No matching Gajala power command
was found in the inspected November traces; no power schedule was listed.

AC sleep=0, standby=0, low-power=0, wake-on-network=1; idle system sleep is already
disabled. autorestart=0. Enabling it with sudo -n failed because an admin password
is required; no setting changed. FileVault is off, automatic login is not set,
and services are user LaunchAgents, so power-on alone may not restore service
before an owner login. Login security was not weakened.

Owner actions: enable Energy -> Start up automatically after a power failure;
check the power cable/outlet and obtain Apple hardware diagnostics if resets
recur. Consider power protection after determining whether mains interruption
is involved. Do not perform a disruptive power-cycle test while automation is
needed. Boot-level service migration needs a separate reviewed design: secrets,
user Keychain, session capabilities and authentication cannot simply be moved
into a privileged daemon.

## Verification

Eleven focused incident regressions pass. Full server suite: 523 passed, one
existing failure. The identical trading test fails on untouched live #78:
test_notification_reply_approves_through_the_api directly passes FastAPI's
Header default object instead of a paired device key. Its approval guard was
not weakened to make the test pass. Live financial actions were not tested.

Primary references:
- https://code.claude.com/docs/en/cli-reference (native auth status and commands)
- https://code.claude.com/docs/en/authentication (credential storage)
- https://support.apple.com/en-mide/guide/mac-help/change-energy-settings-mchlp1168/26/mac/26

Updated the official Claude CLI from 2.1.299 to 2.1.326. A post-update,
tool-free provider check still failed OAuth refresh; owner login is required.
The update is not claimed to fix recurring token invalidation. Resumed-session
Claude estimates are no longer added to per-turn cost totals; their visible
footer explicitly labels them as a session estimate, not an invoice.

Owner completed a new sign-in during the investigation. A subsequent real,
tool-free Claude provider request returned OK. Current provider access is restored;
this does not establish why earlier refresh credentials were invalidated.
