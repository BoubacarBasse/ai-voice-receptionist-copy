# Onboarding a new shop

The interview, in the order worth asking it. Each question maps to a field in `client.yml`.

**The rule that matters most: if the owner doesn't know, write nothing.** A blank field makes
Makati say "I'd have to check with him" and take a callback — which is correct, and is the single
most valuable behaviour in the system. A guess becomes something she states to a customer with
total confidence. the owner's first version invented a repair warranty, a safety-inspection service and
tire storage, and callers believed all three.

---

## Before the call

Ask them to have handy: their business address including any unit number, their hours, and the
phone number customers currently ring.

---

## 1. The business — 2 minutes

| Ask | Field | Why it matters |
|---|---|---|
| "What's the shop called — exactly as you'd want it answered on the phone?" | `business.name` | She says it in the greeting on every call |
| "And your name? What do customers call you?" | `business.owner_name` | Used ~130 times across her prompts. This is the single most-substituted value |
| "Full address, including a unit number if there is one?" | `business.address` | Include the unit. The pilot shop's customers were driving past because his is at the back |
| "What city?" | `business.city` | Used for seasonal/climate advice in the car knowledge |
| — | `business.timezone` | IANA, e.g. `America/Winnipeg`. Drives cron schedules **and** spoken times. Get it wrong and the morning text arrives at 3 AM |
| — | `infra.n8n_host` | The n8n instance hosting the workflows. Every webhook URL in the flow points here — wrong value and she tells callers a booking worked that never left the building |
| — | `infra.screener_agent_id` | The Retell agent that announces the caller to the owner on a transfer. Create it before you render |

Pick the **slug** yourself: lowercase, no spaces. It prefixes every webhook path and workflow name.
Two clients sharing an n8n instance with the same slug collide.

## 2. Hours — 5 minutes, and worth every one

| Ask | Field |
|---|---|
| "What are your hours? Say it how you'd say it to a customer." | `hours.spoken` |
| "Which days are you closed?" | `hours.closed_days` |
| **"How long does a typical job block the bay?"** | `hours.appointment_minutes` |
| **"What's the latest you'd start a job and still finish it?"** | `hours.last_appointment` |

Those last two are the ones people get wrong. **They must agree with each other and with closing
time**, and `render.py` refuses to build if they don't.

The pilot shop's system shipped with the prompt saying 5 PM and the backend accepting 5:30, and it booked jobs
he could not complete. Ask the second question explicitly — owners answer "we close at six" and
mean "the last job starts at five".

## 3. Services — 10 minutes

> "What do you do here? And what do people ring up asking for that you *don't* do?"

- `services.offered` — free text, whatever he says.
- `services.conditional` — **listen for this one.** Almost every shop has a job it won't do on its
  own but will do alongside other work. For the pilot shop it's oil changes. It has its own prompt block
  because getting her to hold that refusal through a customer pushing back took several rounds of
  work. If the shop has no such service, leave it out and the block disappears.

## 4. Phone numbers — the part that decides whether it works at all

Read the [two-number model](#the-two-number-model) below before this section, and explain it to
the owner **before** he agrees to anything.

| Ask | Field |
|---|---|
| "What number do customers ring today?" | becomes `phone.owner_cell` — and it will forward to Makati |
| "Do you have a second phone, or can you get one?" | `phone.transfer_to` |
| — | `phone.makati_line`, `phone.sms_from` — you provision these in Retell and Twilio |
| "Which phones will you text us back from?" | `phone.sms_allowed` — **list every one** |

`sms_allowed` matches exact numbers. A reply from an unlisted phone is silently rejected and the
owner concludes the system is broken.

## 5. Calendar and email — 5 minutes

**Make a calendar for the shop. Never use anyone's personal one.** Makati reports every timed event
on it to the owner as a booking. The pilot shop's system texted him the builder's own recurring personal events as
customer appointments, because it was reading a shared personal calendar.

Keep the calendar in *your* Google account and share it with the owner as **"Make changes to
events"** — not "manage sharing", and don't transfer ownership. The n8n credential lives in your
account; if he owns the calendar, your access is his to revoke by accident.

`email.owner` gets bookings. `email.alerts` gets failures — deliberately separable, because the
person who fixes a broken credential is usually not the shop owner, and paging an owner about a
Twilio outage teaches him to ignore alerts.

## 6. Knowledge — as long as it takes

Work down the `knowledge:` block. Parking, payment, towing, loaners, after-hours drop-off,
deposits, warranty, diesel, EVs, alignments, tire storage, glass, financing.

**"I'm not sure" is a complete answer. Write `null` and move on.** You can fill it in later with
`update_kb.py` in about five seconds while he watches, which is a good moment in the meeting.

---

## The two-number model

| | Role | Forwards? |
|---|---|---|
| **The shop's existing number** | Stays the public line — customers keep dialling what's on the sign. **Also where every text lands**: booking alerts, the morning schedule, the callback digest. Forwarding affects calls only; SMS is untouched | **Yes → the Retell line** |
| **A second number** | Only so Makati has somewhere to ring on a transfer. Doubles as the owner's personal line | **Never.** That's the loop |

**Say this to the owner before he sets the forward:** it forwards *everything*. Once it's on, no
inbound call reaches that phone — not his wife, not his parts supplier. They all get Makati and get
screened. That's the product working, but it feels wrong on day one if nobody warned him.

Framing the second phone as **his personal line** makes it something he wants rather than tolerates.

**Order matters.** Point transfers at the second line *first*, then set the forward. Backwards,
Makati dials the forwarded number, the forward returns the call to her, and it loops until the
timeout drops the caller into a callback.

---

## Then

```
python3 provision/render.py clients/<slug>.yml --check   # config sane?
python3 provision/render.py clients/<slug>.yml           # build it
                                                          # READ build/<slug>/global_prompt.txt
python3 provision/deploy.py clients/<slug>.yml --dry-run
python3 provision/deploy.py clients/<slug>.yml
                                                          # connect credentials in n8n
python3 provision/verify.py clients/<slug>.yml
```

Read the rendered global prompt before deploying. It is what she will say to real customers, it is
readable English, and it is the last cheap moment to catch a wrong fact.

## Go-live, in order

1. Credentials connected in n8n, workflows activated
2. Retell number bought and bound to the agent
3. Recording retention set to match `compliance.recording_retention_days`
4. Transfers pointed at the second line
5. `verify.py` green
6. **The four live calls** — the four scenarios the pilot's TESTING log signs off by handset. Those four
   caught real bugs on the pilot shop's system and they are worth fifteen minutes on every new shop
7. **Only then**: the owner sets the call forward
