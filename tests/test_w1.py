#!/usr/bin/env python3
# Adapted from the pilot's tests for the template. The originals belong to the live pilot and stay private.
# Reads the rendered client config rather than hardcoded values:
#     python3 tests/test_w1.py clients/<slug>.yml
import json as _json, os as _os, sys as _sys
def _client():
    a=[x for x in _sys.argv[1:] if not x.startswith('-')]
    if not a: _sys.exit('usage: test_w1.py clients/<slug>.yml')
    _sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)),
                                      _os.pardir, 'provision'))
    from render import load_yaml
    cfg = load_yaml(a[0])
    return cfg
CLIENT = _client()
SLUG = CLIENT['slug']
"""Signed end-to-end tests for the Appointment Logger webhook."""
import json, hmac, hashlib, os, sys, time, urllib.request, datetime

KEY = os.environ['RETELL_API_KEY']
URL = 'https://%s/webhook/%s-appointment' % (CLIENT['infra']['n8n_host'], SLUG)

def call(body):
    raw = json.dumps(body, separators=(',', ':'))
    ts = str(int(time.time() * 1000))
    d = hmac.new(KEY.encode(), (raw + ts).encode(), hashlib.sha256).hexdigest()
    req = urllib.request.Request(URL, data=raw.encode(), method="POST", headers={
        "Content-Type": "application/json",
        "x-retell-signature": f"v={ts},d={d}"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read() or b'{}')
    except urllib.error.HTTPError as e:
        body = e.read()
        try: return e.code, json.loads(body)
        except Exception: return e.code, {"raw": body[:200].decode(errors='replace')}

# next business day (skip Sunday)
d = datetime.date.today() + datetime.timedelta(days=1)
while d.weekday() == 6:  # Sunday
    d += datetime.timedelta(days=1)
DATE = d.isoformat()
# next Sunday for outside-hours test
s = datetime.date.today() + datetime.timedelta(days=1)
while s.weekday() != 6:
    s += datetime.timedelta(days=1)
SUNDAY = s.isoformat()
# A second, quieter day for the caller-ID tests — DATE collects real bookings and leftover
# test events, and a slot conflict there would look like a phone-resolution failure.
d2 = d + datetime.timedelta(days=7)
while d2.weekday() == 6:
    d2 += datetime.timedelta(days=1)
DATE2 = d2.isoformat()

CID = "test_sprint_" + str(int(time.time()))
ARGS = {"customer_name": "Test Sprint", "phone": "204-555-0142",
        "vehicle": "2015 Toyota Corolla", "service": "brake inspection"}

results = []
def check(name, status, resp, ok):
    results.append((name, status, ok, resp))
    print(("PASS " if ok else "FAIL ") + name, "|", status, "|", json.dumps(resp)[:180])

# 1. get_current_datetime
st, r = call({"name": "get_current_datetime", "args": {}, "call": {"call_id": CID}})
check("datetime", st, r, st == 200 and "current_datetime" in r)

# 2. book. One taken slot used to abort the whole suite — and a real customer booking at 10:00 is
#    not a reason to stop testing. Walk a few opening times before giving up; SLOT is then reused
#    by the idempotency check below so both halves stay on the same appointment.
SLOT = None
for candidate in ("10:00", "09:30", "13:00", "14:30", "16:00"):
    st, r = call({"name": "log_appointment",
                  "args": {**ARGS, "preferred_date": DATE, "preferred_time": candidate},
                  "call": {"call_id": CID}})
    if st == 200 and r.get("success") is True:
        SLOT = candidate
        break
    if not (r or {}).get("conflict"):
        break          # a real error, not a busy slot — fall through and report it
eid = r.get("event_id")
check("book %s" % (SLOT or "10:00"), st, r,
      st == 200 and r.get("success") is True and r.get("action") == "created" and eid)
SLOT = SLOT or "10:00"

# A blocked calendar makes every later test fail for a reason that has nothing to do with the
# code: W1 checks availability against the WHOLE calendar, so one all-day personal event (a hike,
# a flight, a birthday) blocks every shop slot and cascades ~11 confusing failures. Say it once,
# plainly, instead of letting the reader debug booking logic that is fine.
if not (r or {}).get("success") and (r or {}).get("conflict"):
    alts = (r or {}).get("alternatives") or []
    print("\n" + "!" * 78)
    print("ABORTING: %s has no free slot at any of 10:00, 9:30, 1:00, 2:30 or 4:00." % DATE)
    print("Everything below this depends on that booking, so it would all fail spuriously.")
    print("W1 checks availability against the ENTIRE Google Calendar, not just shop events,")
    print("so ONE all-day personal event blocks the whole day for customers too.")
    if alts:
        print("Next free slots offered: %s" % ", ".join(alts[:2]))
    print("Fix: clear that day, or move Makati onto its own calendar (see NOTES.md).")
    print("!" * 78)
    sys.exit(1)

# 3. duplicate same call_id -> idempotent
st, r = call({"name": "log_appointment", "args": {**ARGS, "preferred_date": DATE, "preferred_time": SLOT},
              "call": {"call_id": CID}})
check("idempotent duplicate", st, r, st == 200 and r.get("action") == "duplicate_skipped")

# 4. conflicting booking, different call
st, r = call({"name": "log_appointment", "args": {**ARGS, "customer_name": "Other Caller", "phone": "204-555-0999",
              "preferred_date": DATE, "preferred_time": "10:00"}, "call": {"call_id": CID + "_b"}})
check("conflict alternatives", st, r, st == 200 and r.get("conflict") is True and isinstance(r.get("alternatives"), list))

# 5. find by phone
st, r = call({"name": "find_appointment", "args": {"phone": "2045550142"}, "call": {"call_id": CID + "_f"}})
check("find by phone", st, r, st == 200 and r.get("found") is True and r.get("event_id") == eid)

# 6. reschedule
st, r = call({"name": "update_appointment", "args": {"event_id": eid, "new_date": DATE, "new_time": "11:00"},
              "call": {"call_id": CID + "_u"}})
check("reschedule 11:00", st, r, st == 200 and r.get("success") is True and r.get("action") == "updated")

# 7. outside hours (Sunday)
st, r = call({"name": "log_appointment", "args": {**ARGS, "preferred_date": SUNDAY, "preferred_time": "10:00"},
              "call": {"call_id": CID + "_s"}})
check("closed Sunday", st, r, st == 200 and r.get("outside_clinic_hours") is True)

# 8. outside hours (evening)
st, r = call({"name": "log_appointment", "args": {**ARGS, "preferred_date": DATE, "preferred_time": "19:00"},
              "call": {"call_id": CID + "_e"}})
check("after 6pm rejected", st, r, st == 200 and r.get("outside_clinic_hours") is True)

# 9. log_callback
st, r = call({"name": "log_callback", "args": {"customer_name": "Test Sprint", "phone": "204-555-0142",
              "vehicle": "2015 Toyota Corolla", "reason": "wants Sam about brakes", "preferred_time": "afternoon"},
              "call": {"call_id": CID + "_cb"}})
check("log_callback", st, r, st == 200 and r.get("success") is True)

# --- 2026-07-25 regressions: caller-ID lookup, multi-match, junk event_id, no empty body ---

# 9a. find with NO phone argument — must fall back to the number the caller is dialling from
st, r = call({"name": "find_appointment", "args": {},
              "call": {"call_id": CID + "_cid", "from_number": "+12045550142"}})
check("find by caller ID (no phone arg)", st, r,
      st == 200 and r.get("found") is True and r.get("phone_source") == "caller_id")

# 9b. a second booking on the same number must produce a choice, not a silent first-match.
#     This needs a free afternoon slot, and the shop books against the owner's PERSONAL calendar —
#     on 2026-08-06 a personal event covered Friday from 2 PM and turned this and the two checks
#     that depend on it into three red lines that had nothing to do with the code. A conflict here
#     is the calendar being busy, not a defect, so say so instead of failing.
st, r = call({"name": "log_appointment", "args": {**ARGS, "preferred_date": DATE, "preferred_time": "15:00"},
              "call": {"call_id": CID + "_m"}})
eid2 = r.get("event_id")
blocked = st == 200 and r.get("conflict") is True
if blocked:
    print("SKIP second booking same phone | the 3 PM slot on %s is taken on the calendar "
          "(alternatives offered: %s) — multi-match needs two live bookings, so it and the "
          "update-404 check are skipped too. Not a code failure." % (DATE, r.get("alternatives")))
else:
    check("second booking same phone", st, r, st == 200 and r.get("success") is True and eid2)

    st, r = call({"name": "find_appointment", "args": {},
                  "call": {"call_id": CID + "_multi", "from_number": "+12045550142"}})
    check("multi-match returns choices", st, r,
          st == 200 and r.get("multiple") is True and r.get("count", 0) >= 2
          and isinstance(r.get("options"), list) and bool(r.get("choices")))

# 9c. the literal string that broke production on 2026-07-25 must never reach Calendar
st, r = call({"name": "update_appointment", "args": {"event_id": "active", "new_date": DATE, "new_time": "16:00"},
              "call": {"call_id": CID + "_junk"}})
check("junk event_id 'active' guarded", st, r,
      st == 200 and r.get("success") is False and r.get("needs_lookup") is True)

# 9d. a real Calendar failure must still answer with parseable JSON — never an empty body,
#     which is what stalled the agent mid-call
st, r = call({"name": "cancel_appointment", "args": {"event_id": "nonexistent999notreal"},
              "call": {"call_id": CID + "_404"}})
check("calendar 404 -> JSON not empty body", st, r,
      st == 200 and isinstance(r, dict) and r.get("success") is False
      and r.get("error") == "calendar_unavailable")

# 9e. the cancel-side placeholder guard (the update side is covered by 9c)
st, r = call({"name": "cancel_appointment", "args": {"event_id": "latest"},
              "call": {"call_id": CID + "_junk2"}})
check("junk event_id 'latest' guarded (cancel)", st, r,
      st == 200 and r.get("success") is False and r.get("needs_lookup") is True)

# 9e-bis. 2026-08-04: the blacklist only ever covered the five words someone thought of.
#     call_a332bb48 sent event_id "Wednesday" — the caller's word for their appointment — and it
#     sailed past the guard into Google Calendar, which 404'd. The caller got "I'm having trouble
#     cancelling that, let me get Sam" and a transfer, for input that was never an ID.
#     These are shapes, not words: every one must come back as needs_lookup, and none of them may
#     reach Calendar (a calendar_unavailable here means the guard let it through).
for junk in ("Wednesday", "today", "my appointment", "tomorrow at 2", "the one at two",
             "myappointmentfortomorrow", "next week"):
    st, r = call({"name": "cancel_appointment", "args": {"event_id": junk},
                  "call": {"call_id": CID + "_sh_" + junk.replace(" ", "_")}})
    check("spoken event_id %r -> needs_lookup" % junk, st, r,
          st == 200 and r.get("needs_lookup") is True
          and r.get("error") != "calendar_unavailable")

# 9e-quater. 2026-08-05 21:06: she skipped the lookup entirely and invented an id —
#     "1b1b1b1b…" sixty-two characters of it. Long, no spaces, plenty of digits, so the shape
#     guard waved it through to Calendar. Two more bounds, both taken from what Google actually
#     issues (26 base32hex characters): a length ceiling, and a minimum number of distinct
#     characters. A made-up id has almost no alphabet; a real one has fifteen or more.
for label, junk in (("the 08-05 fabrication", "1b" * 31),
                    ("same pattern, id-length", "1b" * 13),
                    ("three chars on repeat", "abc" * 8 + "1"),
                    ("too long to be an event id", "a1b2c3d4e5f6g7h8j9k0m1n2p3q4r5s6t7v8w9x0y1z2")):
    st, r = call({"name": "cancel_appointment", "args": {"event_id": junk},
                  "call": {"call_id": CID + "_lowent"}})
    check("%s -> needs_lookup" % label, st, r,
          st == 200 and r.get("needs_lookup") is True
          and r.get("error") != "calendar_unavailable")

# 9e-ter. the guard must not become so tight that a REAL id bounces. An id Calendar doesn't know
#     still has to reach Calendar and come back as calendar_unavailable, not as needs_lookup —
#     otherwise a genuine cancel would silently turn into a lookup forever. Both of these are
#     shaped exactly like ids we have really created.
for label, real in (("26-char base32hex", "fgoc0l8hedqm96j14hldaq00qo"),
                    ("mixed 21-char", "nonexistent999notreal")):
    st, r = call({"name": "cancel_appointment", "args": {"event_id": real},
                  "call": {"call_id": CID + "_shape_ok"}})
    check("real-shaped event_id (%s) still reaches Calendar" % label, st, r,
          st == 200 and r.get("needs_lookup") is not True
          and r.get("error") == "calendar_unavailable")

# 9f. an Update Event failure must come back as JSON too — this exercises the error output
#     on the reschedule branch, which had no direct coverage
st, r = call({"name": "update_appointment",
              "args": {"event_id": "nonexistent999notreal", "new_date": DATE, "new_time": "14:00"},
              "call": {"call_id": CID + "_u404"}})
if st == 200 and r.get("conflict") is True:
    # Availability is checked before the event is fetched, so a busy 2 PM answers "conflict"
    # and this branch never reaches Calendar. Busy calendar, not a broken error path.
    print("SKIP update 404 -> JSON not empty body | 2 PM on %s is taken, so the reschedule "
          "never reached Calendar. Not a code failure." % DATE)
else:
    check("update 404 -> JSON not empty body", st, r,
          st == 200 and isinstance(r, dict) and r.get("success") is False
          and r.get("error") == "calendar_unavailable")

# --- 2026-07-26 regressions: the write path must resolve a phone the same way the read path does ---

# 9g. THE regression. call_15256f1a booked with phone:"" because the caller said "this number
#     right here"; the event went to Calendar with a blank phone field and call_a7c52540 could
#     never find it again. Book with no phone, then look up with no phone — it must come back.
CID_CID = CID + "_blank"
FROM = "+12045550188"
st, r = call({"name": "log_appointment",
              "args": {**ARGS, "customer_name": "Blank Phone", "phone": "",
                       "preferred_date": DATE2, "preferred_time": "13:00"},
              "call": {"call_id": CID_CID, "from_number": FROM}})
eid3 = r.get("event_id")
check("book with empty phone", st, r, st == 200 and r.get("success") is True and eid3)

st, r = call({"name": "find_appointment", "args": {},
              "call": {"call_id": CID_CID + "_f", "from_number": FROM}})
check("empty-phone booking is findable by caller ID", st, r,
      st == 200 and r.get("found") is True and r.get("event_id") == eid3
      and r.get("phone_source") == "caller_id")

# 9h. call_19a2983a sent phone:"caller" — a placeholder, not a number. It must never be stored
#     or searched on; the caller ID stands in for it, same as an empty string.
# 15:00, not 13:30. Appointments are 60 minutes as of patch44, so a 13:30 booking now
# collides with the 13:00 one above — correctly. Keep these two slots an hour apart.
CID_JUNK = CID + "_junkphone"
FROMJ = "+12045550177"
st, r = call({"name": "log_appointment",
              "args": {**ARGS, "customer_name": "Junk Phone", "phone": "caller",
                       "preferred_date": DATE2, "preferred_time": "15:00"},
              "call": {"call_id": CID_JUNK, "from_number": FROMJ}})
eid4 = r.get("event_id")
check("book with phone 'caller'", st, r, st == 200 and r.get("success") is True and eid4)

st, r = call({"name": "find_appointment", "args": {"phone": "caller"},
              "call": {"call_id": CID_JUNK + "_f", "from_number": FROMJ}})
check("placeholder phone falls back to caller ID", st, r,
      st == 200 and r.get("found") is True and r.get("event_id") == eid4
      and r.get("phone_source") == "caller_id")

# 9i. a caller who states a DIFFERENT number still wins over caller ID
st, r = call({"name": "find_appointment", "args": {"phone": "204-555-0188"},
              "call": {"call_id": CID_CID + "_stated", "from_number": FROMJ}})
check("stated number beats caller ID", st, r,
      st == 200 and r.get("found") is True and r.get("event_id") == eid3
      and r.get("phone_source") == "stated")

for _eid, _tag in ((eid3, "_c3"), (eid4, "_c4")):
    if _eid:
        call({"name": "cancel_appointment", "args": {"event_id": _eid},
              "call": {"call_id": CID + _tag}})

# 10. cancel both (cleanup)
st, r = call({"name": "cancel_appointment", "args": {"event_id": eid}, "call": {"call_id": CID + "_c"}})
check("cancel/cleanup", st, r, st == 200 and r.get("success") is True and r.get("action") == "cancelled")

if eid2:
    st, r = call({"name": "cancel_appointment", "args": {"event_id": eid2}, "call": {"call_id": CID + "_c2"}})
    check("cancel/cleanup 2", st, r, st == 200 and r.get("success") is True)

# 11. bad signature rejected
raw = json.dumps({"name": "get_current_datetime", "args": {}, "call": {"call_id": "x"}}, separators=(',', ':'))
ts = str(int(time.time() * 1000))
req = urllib.request.Request(URL, data=raw.encode(), method="POST", headers={
    "Content-Type": "application/json", "x-retell-signature": f"v={ts},d=" + "0" * 64})
try:
    with urllib.request.urlopen(req, timeout=60) as r2:
        st, rr = r2.status, json.loads(r2.read() or b'{}')
except urllib.error.HTTPError as e:
    st, rr = e.code, json.loads(e.read() or b'{}')
check("bad signature -> 401", st, rr, st == 401)

fails = [x for x in results if not x[2]]
print(f"\n{len(results)-len(fails)}/{len(results)} passed")
if fails:
    for f in fails: print("FAILED:", f[0], f[1], json.dumps(f[3])[:400])
    raise SystemExit(1)
