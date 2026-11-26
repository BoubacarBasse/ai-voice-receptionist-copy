# AI Voice Receptionist for Auto Repair Shops

**An AI phone agent that ran in production for five weeks, answering real customer calls for an
independent auto shop, packaged as a template that can be set up for a new shop from one config file.**

She answers the calls the owner misses. She books, reschedules and cancels appointments on the
shop's live Google Calendar and takes callbacks. When a caller asks for the owner, she finds out
why first, and she texts the owner what he needs to know. She will not quote a price and she will
not make up a fact.

> This repo is the **reusable template**. The pilot client's own deployment, call logs and build
> history stay private because they hold the client's data, and the client is anonymised as "the
> pilot shop" throughout. The pilot ran 2026-08-15 to 2026-09-23. I switched it off on purpose —
> [here's why](#what-the-pilot-proved-and-why-i-switched-it-off).

---

## Results from the live pilot

| | |
|---|---|
| **Ran in production** | 2026-08-15 to 2026-09-23 — five weeks on the shop's real public number. It rang the owner first and she picked up what he missed |
| **Factual accuracy** | **12/25 → 29/29** on the grounding suite, since grown to 38 cases. Before the fix she invented a repair warranty, a safety-inspection service and tire storage, and callers believed all three |
| **Scale of the build** | 8 production n8n workflows · 102 nodes · a 36-node conversation flow · 6 backend tools · 5 external systems |
| **Running cost** | **$47.85** for the go-live month, everything in. $26.94 of that was LLM text at $0.015 a message — over half the bill, and more than the voice minutes |

## What the pilot proved, and why I switched it off

The system worked. The problem I picked didn't.

In five weeks she answered 81 calls from numbers outside the shop. 19 of those callers said a
word. Three calls produced anything at all: one booking, one sales lead, one transfer that
connected. The month's bill was $47.85.

The reason is in the call log. A missed call only costs a shop money if the caller is new and the
job can't wait. At an established shop, almost nobody is either. They're regulars, their work can
wait, and when nobody picks up they just call back.

One regular called four times over 18 days and never left a message once — *"It's just Mark
calling. Nothing really important. I'll get back to you later."* Another number called four times,
three of them inside 70 minutes. A third called five times and never said a word. The people who
wanted the owner were going to reach him with or without me. The missed call was a delay, not a
lost customer.

Two other things worth knowing. Most of the volume wasn't customers: in the last month of the
pilot, 32 of 75 answered calls came from out-of-province area codes, nearly all hanging up at the
same 17-second mark. And the cost wasn't where I expected it — $26.94 of that $47.85 was LLM text
at $0.015 a message. What sets the unit economics of a voice agent is cost per turn, not talk time.

So I switched it off instead of tuning it further. The same system is worth a lot more pointed
somewhere else. Missed-call recovery pays when the caller is new and the job can't wait. Regulars
call back; someone with no heat in January calls the next contractor. That's the version I'm
building now, for HVAC.

## Architecture

```
caller ──► shop number (forwards on no-answer/busy)
             └─► Retell voice agent ── 36-node conversation flow
                   ├─ knowledge base (retrieval)  → the only facts she may state
                   └─ 6 tools ─► ONE n8n webhook (HMAC-verified)
                                   ├─ Google Calendar   book / reschedule / cancel / find
                                   ├─ Google Sheets     call log, callback queue
                                   ├─ Twilio SMS        owner alerts, 8:45 AM schedule, callback digest
                                   └─ SMTP              summaries, failure alerts (no customer data)
```

**Retell runs the conversation. n8n handles everything that gets saved or sent.** The agent cannot write to
a calendar or send a text directly. Every action goes through a webhook that checks an HMAC
signature first. The owner can also text back (a name, `all`, `undo`) to clear his callback queue.

| Workflow | Trigger | Job |
|---|---|---|
| W1 Appointment Logger | webhook | book, reschedule, cancel, find, callback |
| W2 Error Handler | error workflow | dead-letter log plus an alert, with no customer data |
| W3 Call Log & Notify | webhook | logs every call and texts the owner a one-line summary |
| W4 Daily Callback Digest | 9:00 AM cron | who is still owed a callback |
| W5 Callback Reply Parser | webhook | the owner's text replies |
| W6 Sheet Init | manual, once | creates the spreadsheet |
| W7 Secret Bridge | webhook | gives a credential to the workflow that needs it |
| W8 Morning Schedule | 8:45 AM cron | today's jobs, texted before the shop opens |

## The parts that were hard

The happy path was quick to build. Most of the work went into failures that only showed up on real calls.
[ARCHITECTURE.md](ARCHITECTURE.md) explains each guardrail and the incident that led to it. Four examples:

- **She said it was done when nothing had happened.** A caller refused to give a reason, and she said
  *"the owner will call you back tomorrow at 9."* The function that logs the callback never ran.
  The fix: **the tests check which tools the agent called, not what it said.** A script that ends
  with the right words but no tool call counts as a failure.
- **She treated an empty response as success.** Every route out of a function node checked for
  a field in the response. An empty response matched none of them, so the model picked a route
  and told the caller the booking was confirmed. Now an empty response counts as a failure.
- **She treated the knowledge base as a starting point to build on.** The fix was to make the
  knowledge base a hard limit: if a field is blank she says *"I'd have to check"* and logs a callback.
  A blank field is safe. A guess is not.
- **She could ring the owner twice on one call.** After the owner declined a transfer, a global
  "caller wants a human" node could send the call back into a second transfer. Now the owner is rung
  at most once per call, on every path, and a test checks it.

## Testing

Four suites, in [`tests/`](tests):

| Suite | What it proves |
|---|---|
| `test_flow.py` | Structure of the flow: every function node handles failure, triage never stands between a failed booking and the owner, the owner can't be rung twice |
| `test_w1.py` | Backend transactions against the real calendar: booking, duplicate-call idempotency, conflicts with alternative times offered, find, reschedule, cancel, junk-ID guards, callbacks |
| `test_kb.py` | Factual grounding: 38 conversations that try to get her to make something up, including prices, durations and services the shop doesn't offer |
| `test_calls.py` | Full multi-turn conversations through the real agent. It checks which tools fired and which nodes were reached |

Some things can only be tested on a real phone (transfers, SMS replies). Those were signed off on live calls.

## Setting up a new shop

Everything specific to the shop lives in a single `client.yml`. The renderer **refuses to run if a
field is missing** instead of filling in a default, because a default is something the agent would
tell a customer as fact.

```bash
cp client.example.yml clients/<slug>.yml            # fill it in from the owner interview
python3 provision/render.py clients/<slug>.yml --check
python3 provision/render.py clients/<slug>.yml      # → build/<slug>/ flow, prompt, workflows, KB
python3 provision/deploy.py clients/<slug>.yml --dry-run
python3 provision/deploy.py clients/<slug>.yml
python3 provision/verify.py clients/<slug>.yml      # sends a signed test call end to end
```

[ONBOARDING.md](ONBOARDING.md) is the owner interview: each question, the field it fills, and
why it matters. I also kept a private fidelity check that renders the pilot's config and compares
it against the system that was deployed, so the template can't drift away from the version that
ran.

## How I built it

I did the client work and the system design myself: discovery with the shop owner, the
conversation design, deciding which guardrails exist and why, the test strategy, deployment and
go-live, and reading the live call logs every day to find the next fix. I wrote the code with
**Claude Code** as my pair programmer, working from specifications and failure reports I wrote.
I worked this way on purpose. A small team using AI tools can design, build, test, deploy and run
a system like this in production, and read the data well enough to know when to stop.

**Stack:** Retell AI · n8n · Twilio · Google Calendar & Sheets API · Python · Claude Code

---

*Boubacar Basse, Winnipeg · [LinkedIn](https://www.linkedin.com/in/boubacar-basse)*
