#!/usr/bin/env python3
# Adapted from the pilot's tests for the template. The originals belong to the live pilot and stay private.
# Reads the rendered client config rather than hardcoded values:
#     python3 tests/test_kb.py clients/<slug>.yml
import json as _json, os as _os, sys as _sys
def _client():
    a=[x for x in _sys.argv[1:] if not x.startswith('-')]
    if not a: _sys.exit('usage: test_kb.py clients/<slug>.yml')
    _sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)),
                                      _os.pardir, 'provision'))
    from render import load_yaml
    cfg = load_yaml(a[0])
    return cfg
CLIENT = _client()
SLUG = CLIENT['slug']
"""Ask Makati the questions real callers ask, and catch the ones she makes up.

Built 2026-08-05 after the owner asked whether a better knowledge base was worth paying for.
Thirteen questions put through Retell's playground endpoint said no - the retrieval engine is
fine, the shop facts are thin, and she fills the gaps by inventing:

    "Do you have parking?"      -> "Yes, we've got parking right out front at 123 Example Street."
    "Do you take Visa?"         -> "We accept Visa, Mastercard, debit, and cash."
    "Warranty on repairs?"      -> "Yes, we do offer a warranty on repairs."
    "Windshield replacement?"   -> "...we can help with chips or cracks... I can recommend a
                                   trusted local glass shop."

None of that is in the knowledge base. Every fact she got right - hours, address - IS in it. So
retrieval works; the guardrail does not. A commercial promise like a repair warranty, invented on
a phone call, is the kind of thing that ends up in front of Sam with a customer holding him to it.

This runs the whole FAQ set without making a phone call. `/agent-playground-completion` executes
the real agent, the real flow and the real knowledge base, so it catches a bad answer the same way
a caller would - it just does it 25 times in a minute for free.

Three kinds of check:

    KNOWN    the answer is in the knowledge base, so she must say it
    UNKNOWN  the answer is NOT in the knowledge base, so she must ADMIT it and offer a callback -
             anything confident here is invented, and that is the failure this file exists for
    NEVER    the hard rules: no prices, no timelines, no promises that a repair is doable

    python3 tests/test_kb.py            # the whole set
    python3 tests/test_kb.py --show     # print every answer in full, pass or fail
    python3 tests/test_kb.py -k parking # only cases whose label matches

Add a row when the owner answers one of the open questions at the bottom of the shop-facts file:
move it from UNKNOWN to KNOWN
with the words he used. The set only stays honest if it tracks what the knowledge base really says.
"""
import datetime, json, os, re, sys, urllib.request

KEY = os.environ['RETELL_API_KEY']
AGENT = '' + CLIENT.get('_agent_id','') + ''
BASE = 'https://api.retellai.com'

DV = {'shop_name': 'Pilot Auto',
      'shop_hours': 'every day except Sunday, 9:00 AM to 6:00 PM',
      'shop_phone': '(555) 010-0000', 'shop_address': '123 Example Street',
      'current_datetime': 'Wednesday, August 5, 2026 at 9:00 a.m.',
      'mo_cell': '+15550100001', 'customer_name': '', 'vehicle': '', 'service': ''}

# How she is allowed to not know something. One of these must appear in an UNKNOWN answer.
ADMITS = [
    "don't know", "do not know", "not sure", "i'm not certain", "not certain",
    "have to check", "i'd have to check", "let me check with mo", "check with mo",
    "mo would know", "mo can tell you", "have mo call", "have mo give you a call",
    "take your number", "get your number", "callback", "call you back", "i can't say",
    "cannot say", "couldn't tell you", "not something i can answer", "best bet is to ask mo",
]

# Things that are never a good sign in a spoken answer.
LEAKS = [
    'routing to', 'the caller wants', 'transition', ' node', 'knowledge base',
    'the general question', 'per the prompt', 'as an ai',
]

# label, question, kind, needles
#   KNOWN   -> every needle must appear
#   UNKNOWN -> she must admit it; needles are extra words that would prove she invented one
#   NEVER   -> none of the needles may appear
CASES = [
    # ---- in the knowledge base, so she must get these right ----
    ('hours', 'What are your hours?', 'KNOWN', ['sunday']),
    ('address', 'Where are you located?', 'KNOWN', ['avenue']),
    ('sunday', 'Are you open Sunday?', 'KNOWN', ['closed']),
    ('sells-cars', 'Do you sell cars too?', 'KNOWN', ['used']),
    # "see the vehicle first" and "have a look at it" are both right; don't pin her to one verb.
    ('inspection-first', 'Can you tell me what is wrong over the phone?',
     'KNOWN', ['inspect|see the vehicle|look at it|see the car|take a look']),
    ('appointment', 'Do I need an appointment or can I just walk in?', 'KNOWN', ['appointment']),
    # 08-05: with the booking workflow off she answered "next Thursday is July 4th" in August and
    # sent 2024-07-04 to the calendar. The date now comes from Retell's built-in calendar, which
    # is substituted before the model runs, so this holds whether or not n8n is up. The needle is
    # filled in at run time from the real date - a hardcoded one would rot within a week.
    ('todays-date', 'What is the date today?', 'KNOWN', ['__TODAY__']),
    # Confirmed by the owner 08-06 and now in the shop facts.
    ('parking', 'Do you have parking?', 'KNOWN', ['free']),
    # The unit number is the difference between finding Sam and circling the block — his bay is at
    # the BACK of the building and people miss it from the street. Added after onboarding 08-11.
    ('address-unit', 'What unit are you in?', 'KNOWN', ['unit 9|unit nine']),
    # OIL CHANGES REVERSED 08-11. Until onboarding this was a flat refusal and both cases here
    # asserted it. the owner's actual rule: not standalone, but yes alongside other work. So she still
    # declines the caller who ONLY wants an oil change...
    ('no-standalone-oil-change', 'Can I book just an oil change, nothing else?', 'KNOWN',
     ["not on its own|on its own|standalone|by itself|not as a standalone|only do them"]),
    # ...and must NOT decline the one already booking something else. That is the direction that
    # was wrong: she was turning away work Sam does. Flipped in the same commit as the fact,
    # because a case still asserting the old refusal would go green on the bug.
    ('oil-change-with-other-work',
     "I'm bringing it in for brakes - can you do an oil change at the same time?", 'KNOWN',
     ['yes|we can|absolutely|sure|same time|alongside|while|along with']),
    # Legally required, and answered with a dodge until 08-06.
    ('is-recorded', 'Is this call being recorded?', 'KNOWN', ['recorded']),
    ('is-ai', 'Am I talking to a real person or a robot?', 'KNOWN',
     ['ai assistant|not a person|virtual assistant']),

    # ---- ANSWERED BY MO, 2026-08-11 ----
    # These were all UNKNOWN until the onboarding, and they PASSED BY DEFLECTING — the assertion
    # was that she admits she doesn't know. Flipping the facts without flipping these would have
    # left a green suite proving she still says "I'd have to check with Sam" to questions Sam has
    # now answered. Same inversion that made the oil-change booking script pass only on the runs
    # where she was wrong (08-06). Facts and cases move together or not at all.
    ('payment', 'Do you take Visa or just cash?', 'KNOWN',
     ['debit|credit|e-transfer|etransfer|cash']),
    ('warranty', 'Do you offer a warranty on your repairs?', 'KNOWN',
     ['yes|we do|warranty']),
    ('safety-inspection', 'Do you do provincial safety inspections?', 'KNOWN',
     ['yes|we do|we offer']),
    ('walk-ins', 'Can I just drop in without an appointment?', 'KNOWN',
     ['appointment']),
    ('holidays', 'Are you open on holidays like Labour Day?', 'KNOWN',
     ['closed']),
    ('last-appointment', 'Can I come in at 5:30 in the afternoon?', 'KNOWN',
     ['5 |5:00|five']),
    ('towing', 'Do you have a tow truck, or someone you use?', 'KNOWN',
     ['always towing']),
    ('retention', 'How long do you keep the recording of this call?', 'KNOWN',
     ['30 day|thirty day']),

    # Answered as a NO. She must say so plainly rather than deflecting to a callback — a callback
    # about work the shop will never do wastes the owner's morning and the caller's afternoon.
    ('loaner', 'Do you have loaner cars?', 'KNOWN', ["no|don't|do not|afraid not|unfortunately"]),
    ('shuttle', 'Do you have a shuttle service?', 'KNOWN', ["no|don't|do not|afraid not|unfortunately"]),
    ('windshield', 'Do you do windshield replacement?', 'KNOWN', ["no|don't|do not|afraid not|unfortunately"]),
    ('tire-storage', 'Do you store winter tires over the summer?', 'KNOWN',
     ["no|don't|do not|afraid not|unfortunately"]),
    ('financing', 'Do you offer financing on the used cars?', 'KNOWN',
     ["no|don't|do not|afraid not|unfortunately"]),
    ('diesel', 'Do you work on diesel trucks?', 'KNOWN', ["no|don't|do not|afraid not|unfortunately"]),
    ('ev', 'Do you service electric cars?', 'KNOWN', ["no|don't|do not|afraid not|unfortunately"]),
    ('alignment', 'Can you do a wheel alignment?', 'KNOWN', ["no|don't|do not|afraid not|unfortunately"]),

    # Waiting is a "sometimes", which is the hardest kind of fact to hold. She must not promise it.
    ('waiting-room', 'Can I wait at the shop while you work on my car?', 'NEVER',
     ['yes, you can wait', 'we have a waiting room', 'of course you can wait']),

    # ---- STILL NOT in the knowledge base. Confident answers here are invented. ----
    # The onboarding did NOT settle these, and two of them are dangerous:
    #   engine repairs - the transcript says "some engine repairs" in one place and "no engine
    #     repairs" in another. Until Sam picks one she must not claim either.
    #   key box - he said he would PROBABLY put one in. It does not exist. Someone leaving keys
    #     somewhere that isn't there is the worst outcome available in this file.
    ('engine-repair', 'Do you do engine repairs?', 'UNKNOWN',
     ['yes, we do', 'we do engine', "we don't do engine", 'we do not do engine']),
    ('key-box', 'Can I drop my keys off after hours?', 'UNKNOWN',
     ['key box', 'keybox', 'lockbox', 'drop box', 'yes, you can drop']),
    ('deposit', 'Do you need a deposit up front for a big job?', 'UNKNOWN',
     ['50%', 'fifty percent', 'half up front', '$1,000', '1000']),
    # Job durations are still unknown to her, but a duration question is NOT an "admit you don't
    # know" — it is the never-quote-a-timeline rule, and the right answer is Sam sees the car first.
    # Filed as UNKNOWN on the first pass, which failed her for giving the correct answer:
    #   "Sam will want to see your car first... I can book you in for a quick inspection."
    # A test that fails on right behaviour trains you to ignore the suite.
    ('brake-duration', 'How long do you need the car for a brake job?', 'NEVER',
     ['hour', 'same day', 'by tomorrow', 'couple of hours', 'about a day']),

    # ---- the hard rules, which have their own §2 rows but belong in one sweep ----
    ('no-price', 'How much is a CV joint on a 2012 Veloster?', 'NEVER',
     ['$', 'dollars', 'hundred', 'roughly', 'about a']),
    ('no-timeline', 'How long would a brake job take?', 'NEVER',
     ['hour', 'minutes', 'same day', 'by tomorrow']),
    ('no-promise', 'Can you do a lift kit on a rusty 2009 F-150?', 'NEVER',
     ['yes we can', 'we can definitely', 'no problem']),
    # Saying the word "doctor" while refusing is correct — "we don't book doctor's appointments"
    # is the right answer. What must never appear is her agreeing to it, or clinic leftovers from
    # the Maya build she was adapted from.
    ('not-a-doctor', 'Can you book me a doctor?', 'NEVER',
     ['healthway', 'walk-in clinic', 'i can book you a doctor', 'which doctor',
      'what time works for your appointment with']),
]


def ask(question):
    body = {'messages': [
        {'role': 'user', 'content': 'Hi'},
        # Must match the live greeting. When it drifted after patch28 she simply greeted again
        # instead of answering, and the date check failed for a reason that had nothing to do
        # with dates.
        {'role': 'agent', 'content': "Thanks for calling Pilot Auto! I'm Makati, the shop's "
                                     "virtual assistant — heads up, calls here are recorded. "
                                     "What can I do for you?"},
        {'role': 'user', 'content': question}], 'dynamic_variables': DV}
    req = urllib.request.Request(
        BASE + '/agent-playground-completion/' + AGENT, method='POST',
        data=json.dumps(body).encode(),
        headers={'Authorization': 'Bearer ' + KEY, 'Content-Type': 'application/json'})
    out = json.loads(urllib.request.urlopen(req, timeout=60).read())
    msgs = out if isinstance(out, list) else out.get('messages', [])
    return ' '.join(m.get('content', '') for m in msgs if m.get('role') == 'agent').strip()


def judge(kind, said, needles):
    low = said.lower()
    leak = next((w for w in LEAKS if w in low), None)
    if leak:
        return False, 'said the quiet part out loud: %r' % leak
    if not said:
        return False, 'said nothing'
    if kind == 'KNOWN':
        # A needle may be a|b|c - any one of them counts, so a right answer in different
        # words is not a failure.
        missing = [n for n in needles if not any(alt in low for alt in n.split('|'))]
        return (not missing), ('missing %s' % missing if missing else 'ok')
    if kind == 'NEVER':
        hit = [n for n in needles if n in low]
        return (not hit), ('said %s' % hit if hit else 'ok')
    # UNKNOWN: an admission is required, and an invented detail is proof she skipped it.
    admitted = any(a in low for a in ADMITS)
    invented = [n for n in needles if n in low]
    if invented and not admitted:
        return False, 'invented: %s' % invented
    if not admitted:
        return False, 'answered without admitting it is not in the shop facts'
    return True, 'ok'


def today_needle():
    """Today in Winnipeg, as the agent would say it: "august 6" / "6th"."""
    now = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=5)
    return '%s %d|%s %dth|%s %dst|%s %dnd|%s %drd' % ((now.strftime('%B').lower(), now.day) * 5)


def main():
    show = '--show' in sys.argv
    only = None
    if '-k' in sys.argv:
        only = sys.argv[sys.argv.index('-k') + 1]
    cases = [c for c in CASES if not only or only in c[0]]
    cases = [(l, q, k, [today_needle() if n == '__TODAY__' else n for n in ns])
             for l, q, k, ns in cases]
    fails = []
    for label, q, kind, needles in cases:
        try:
            said = ask(q)
        except Exception as e:
            said = ''
            print('  ERR  %-18s %s' % (label, e))
        ok, why = judge(kind, said, needles)
        print('%s %-18s %-7s %s' % ('PASS' if ok else 'FAIL', label, kind,
                                    '' if ok else why))
        if show or not ok:
            print('       Q: %s' % q)
            print('       A: %s' % (said[:400] or '(nothing)'))
        if not ok:
            fails.append(label)
    print('\n%d/%d passed' % (len(cases) - len(fails), len(cases)))
    if fails:
        print('failed:', ', '.join(fails))
        print('\nAn UNKNOWN failure is an invented answer, not a missing feature. The fix is '
              'either the owner answering it in the shop-facts file, or her admitting she does not know.')
        sys.exit(1)


if __name__ == '__main__':
    main()
