# AI Voice Receptionist for Auto Repair Shops

**A production AI phone agent that has been answering real customer calls for an independent auto
shop since August 2026, packaged as a template that can be set up for a new shop from one config file.**

She answers the calls the owner misses. She books, reschedules and cancels appointments on the
shop's live Google Calendar and takes callbacks. When a caller asks for the owner, she finds out
why first, and she texts the owner what he needs to know. She will not quote a price and she will
not make up a fact.

> This repo is the **reusable template**. The live client's own deployment, call logs and build
> history are kept private because they contain the client's data. The client is anonymised as
> "the pilot shop" throughout.

---

## Results from the live pilot

| | |
|---|---|
| **Live since** | 2026-08-15, on the shop's real public number (it rings the owner first and Makati picks up when he can't) |
| **Factual accuracy** | **12/25 → 29/29** on a grounding test suite (now 38 cases). Before the fix she made up a repair warranty, a safety-inspection service and tire storage, and callers believed all three |
| **Scale of the build** | 8 production n8n workflows · 102 nodes · a 36-node conversation flow · 6 backend tools · 5 external systems |
| **Running cost** | about $0.16 per call minute, plus about $2 a month per number. Roughly $150/month at 15 calls a day |

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
it with the system that is actually running, so the template can't drift away from the version
that works.

## How I built it

I did the client work and the system design myself: discovery with the shop owner, the
conversation design, deciding which guardrails exist and why, the test strategy, deployment and
go-live, and reading the live call logs every day to find the next fix. I wrote the code with
**Claude Code** as my pair programmer, working from specifications and failure reports I wrote.
I worked this way on purpose. A small team using AI tools can design, build, test and deploy a
system like this, and keep running it in production.

**Stack:** Retell AI · n8n · Twilio · Google Calendar & Sheets API · Python · Claude Code

---

*Boubacar Basse, Winnipeg · [LinkedIn](https://www.linkedin.com/in/boubacar-basse)*
