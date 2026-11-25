# How Makati works, and why

Applies to both the live pilot system and this template. Most of the guardrails below exist because
something specific went wrong on a real call — several would look like clutter to someone who
hadn't seen the failure, which is why the reasons are here and not just the rules.

---

## The call path

```
caller dials the shop's number
   └── forwards to the Retell number  ← the go-live switch
        └── Retell agent  →  conversation flow (35 nodes)
             ├── knowledge base            what she may state as fact
             └── 6 tools → one n8n webhook → Google Calendar / Sheets / Twilio / SMTP
```

**Retell owns the conversation. n8n owns everything that persists.** She cannot write to a calendar
or send a text directly — every side effect goes through a webhook that verifies a signature first.

### The eight workflows

| | Trigger | Does |
|---|---|---|
| **W1 Appointment Logger** | webhook `<slug>-appointment` | book, reschedule, cancel, find, callback. The one that matters |
| **W2 Error Handler** | errorWorkflow for the rest | dead-letter log + alert email, no PII |
| **W3 Call Log & Notify** | webhook `<slug>-call-log` | logs every call, one-line summary to the owner |
| **W4 Daily Callback Digest** | 9:00 AM cron | who is still owed a callback |
| **W5 Callback Reply Parser** | webhook `<slug>-callback-reply` | the owner texts back: a name, `all`, `undo` |
| **W7 Twilio Secret Bridge** | webhook | hands a credential to a workflow that needs it |
| **W6 Sheet Init** | manual, once | creates the spreadsheet |
| **W8 Morning Schedule** | 8:45 AM cron | today's jobs, texted before the doors open |

---

## The guardrails, and what each one cost

**Never quote a price or a duration.** Book time is not clock time, and a caller who hears "about
an hour" plans their day around it. She refuses and offers an inspection.

**Never state a fact that isn't in the knowledge base.** Before this existed she invented a repair
warranty, a safety inspection service and tire storage. Callers believed all three. Anything
unlisted becomes *"I'd have to check"* plus a callback — and **a blank field is safe, a guess is
not.**

**An empty response is a failure, not a success.** Every edge out of a function node described *a
field in a response*. A response with **no** fields matched none of them — including the error edge
— so the model picked whichever suited the conversation and told the caller their booking was
confirmed. It wasn't. Every writing function now treats silence as failure.

**Never dial the owner twice in one call.** A global "caller wants a human" node could route back
into a second transfer after the first was declined. Nothing would have stopped a third.

**Confirm once, then act.** Two edges leaving one node — "the caller confirmed" and "the caller has
heard the confirmation" — both matched a retraction. The broad edge silently ate the narrow one.

**Don't re-ask a settled question when the system is slow.** A booking stalled ~50 seconds, the
40-second reminder fired, and she asked the caller to confirm a booking they'd already confirmed.
Being asked twice reads as *it didn't work*.

**Guard by shape, not by word list.** A blacklist covers the placeholders someone imagined; callers
produce an unbounded supply of new ones. `event_id: "Wednesday"` reached Google Calendar.

**The screener may not invent, and may not hang up.** It told the owner a caller was an Uber
driver — nobody said that. Asked something it couldn't answer, it ended the call and left the
caller on hold music. When *she* invents, a caller hears something wrong; when the *screener*
invents, **the owner acts on it**.

---

## Traps that have bitten more than once

**Retell publishes. n8n publishes. Both keep a draft, and a GET returns the draft.** Reading back
what you just wrote proves nothing about what runs. Retell: an entire evening's testing ran against
a draft the phone never used. n8n: a workflow's draft held the right phone number while its
published version held the old one. **Check `versionId == activeVersionId`.** And publishing
*freezes* a Retell flow — the next PATCH returns `400 Cannot update published conversation flow`,
and `publish_live.py --new-draft` is the other half of that cycle.

**The Retell API key is also the HMAC secret.** W1 and W3 verify webhooks by computing an HMAC with
it. Rotating it has **two** halves; miss the second and every call 401s silently. The secret lives
in an n8n credential — the bridge workflow only echoes a header back.

**A rebuild script is not a patch.** Re-running one to change a single setting reverts every later
change to that workflow. To change one live value, write a targeted patch that reads state and
edits in place.

**A `.replace()` that misses is a no-op, not an error.** A patch widened the callback-reply
allowlist from one number to a list and, in the same expression, meant to switch the comparison from
`from!==allowed` to `allowed.includes(from)`. The second substring never appeared in the live node —
wrong variable name — so it changed nothing, raised nothing, and the patch printed success. The gate
was left comparing a *string* to an *array*: never equal, so it rejected **every** sender. A patch
that edits code by string substitution must assert the substring was there, and then **execute** the
result rather than re-reading it.

**n8n keys connections by node NAME.** Rename a node without rewriting its connections key and it
orphans: the workflow stays active, logs nothing, and never fires again.

**Cron runs in the workflow's timezone, not UTC.** And anything rendering a time for a human must
name the zone at the call site — `getHours()` turned a 10 AM booking into "3:00 PM".

**A shared calendar reports everything on it.** The morning text sent the owner someone's recurring
personal events as customer bookings. Give the shop its own calendar.

**A grounding rule fixes one agent, not every agent.** The screener was written a day after the
main agent got its no-inventing rule and never received it. When you add a second agent, port the
hard-won rules — they don't travel by themselves.

---

## Why the template provisions from a snapshot

the pilot shop's system has ~45 numbered patches in the private repo. Each is a fix to a specific bug on a
specific day, with the reasoning in its docstring, `--dry-run` and `--revert`.

**The template does not replay them.** It deploys from the exported state — the end result — because
replaying a year of fixes onto a fresh account is slow, fragile and meaningless. The patches remain
as the archive and the reasoning record; the private snapshot-restore tool already proved snapshot
replay works.

## Testing

| | Cost | What it proves |
|---|---|---|
| `test_flow.py` | free | flow structure: reachability, orphan edges, transfer destination, no silent deadlock |
| `test_w1.py` | writes to a real calendar, cleans up | the whole backend: booking, conflicts, caller-ID, the event_id guards |
| `test_kb.py` | **paid** — 38 live conversations | she isn't inventing shop facts |
| `test_calls.py` | **paid** — 18 live conversations | whole conversations; asserts what she *did*, not what she said |

The last two hit the live agent and leave **no Retell call record**, so their cost is invisible
until the invoice. About 170 of them are a large part of how one month's n8n quota disappeared. Run
them when something structural changes.

**And no automated suite replaces four phone calls.** The ones that caught real bugs: ask for a
service the shop refuses standalone, ask for it alongside other work, ask for a time past the last
slot, and make any call ending in a promised callback then check the row actually appeared.
