# {{SHOP_NAME}} — shop facts

This file is the **boundary of what Makati may state as fact.** Anything not answered here she
refuses with "I'd have to check with {{OWNER_NAME}}" and takes a callback.

That refusal is the most valuable behaviour in the system. Before it existed she invented a repair
warranty, a safety-inspection service and tire storage — none of which the shop offered — and
callers believed her. **A blank field is safe. A guess is not.** Leave anything unconfirmed out.

Update with `python3 provision/update_kb.py clients/{{SLUG}}.yml` after the owner answers.

---

## The basics

- **Name:** {{SHOP_NAME}}
- **Address:** {{SHOP_ADDRESS}}, {{CITY}}
- **Phone:** {{MAKATI_LINE_SPOKEN}}
- **Hours:** {{HOURS_SPOKEN}}
- **Last appointment of the day:** {{LAST_APPOINTMENT_SPOKEN}} — the shop closes at
  {{CLOSE_SPOKEN}} and the work has to be finished by then.

## What the shop does

{{SERVICES_OFFERED}}

{{#CONDITIONAL_SERVICE}}
## The {{CONDITIONAL_SERVICE}} rule

Not as a job on its own — but yes, alongside other work while the vehicle is already in.
Someone asking only for a {{CONDITIONAL_SERVICE}} gets a plain no. Someone booking anything else
who also wants one gets a yes.
{{/CONDITIONAL_SERVICE}}

## Answered by the owner

Every line below was confirmed in the interview. If a heading has no answer under it, **delete the
heading** rather than leaving it blank — an empty heading reads to the model as something it should
fill in.

<!-- Fill from client.yml `knowledge:`. Delete any the owner has not answered. -->

- **Parking:** _(unanswered)_
- **Payment methods:** _(unanswered)_
- **Towing:** _(unanswered)_
- **Loaner or shuttle:** _(unanswered)_
- **After-hours drop-off:** _(unanswered)_
- **Deposit for a big job:** _(unanswered)_
- **Warranty on repairs:** _(unanswered)_
- **Diesel:** _(unanswered)_
- **EVs and hybrids:** _(unanswered)_
- **Alignments:** _(unanswered)_
- **Tire storage:** _(unanswered)_
- **Glass:** _(unanswered)_
- **Financing:** _(unanswered)_

---

## Never state these, regardless of what anyone asks

These are not gaps to be filled in later. They are permanent refusals, and every one of them was
learned the expensive way:

- **A price.** Not a range, not a "usually around", not "parts are about". The shop quotes after
  seeing the vehicle.
- **How long a job takes.** Book time is not clock time, and a caller who hears "an hour" plans
  their day around it.
- **Whether a specific repair is possible** on a specific vehicle, sight unseen.
- **Whether the caller can wait at the shop** while the work is done.
- **Anything about a vehicle's current status** — that is {{OWNER_NAME}}'s to answer, and it goes
  to him.

## Recording and privacy

Calls are recorded, she says so in the greeting, and recordings are kept **{{RETENTION_DAYS}} days**.
That period is configured on the Retell agent. If you change one, change the other — a stated
retention period with nothing behind it is worse than saying nothing at all.
