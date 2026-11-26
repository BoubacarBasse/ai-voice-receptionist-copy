#!/usr/bin/env python3
# Adapted from the pilot's tests for the template. The originals belong to the live pilot and stay private.
# Reads the rendered client config rather than hardcoded values:
#     python3 tests/test_calls.py clients/<slug>.yml
import json as _json, os as _os, sys as _sys
def _client():
    a=[x for x in _sys.argv[1:] if not x.startswith('-')]
    if not a: _sys.exit('usage: test_calls.py clients/<slug>.yml')
    _sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)),
                                      _os.pardir, 'provision'))
    from render import load_yaml
    cfg = load_yaml(a[0])
    return cfg
CLIENT = _client()
SLUG = CLIENT['slug']
"""Run whole conversations past Makati and check what she DID, not what she said.

Every serious defect in this project has the same shape: she tells the caller something is done and
the function that would have done it was never called. 4.6 promised a restored appointment. 3.7
promised a transfer. And on 08-06:

    AGENT: And what's it about, Bob? Just a short phrase is fine.
    USER:  I'd rather not say.
    AGENT: Thanks, Bob. Sam will call you back tomorrow at 9:00 AM on this number.
           -> log_callback never ran. No row, no email, no text, no callback.

`test_kb.py` asks one question at a time and reads the answer. That cannot catch this, because the
answer sounded perfect. This file scripts a full back-and-forth through
`/agent-playground-completion` - the real agent, real flow, real prompt - and asserts on the **tool
calls**, which are the only record of anything actually happening.

Each script says which functions must have fired by the end, and which must not. A script that ends
with the right words and no tool call is exactly the failure this exists to catch.

A `@Name` entry in either list means a NODE rather than a function - matched as a case-insensitive
substring of the node labels the API reports, so it works whether Retell sends the id or the name.
Transfer triage (patch31) needed this: its whole claim is that some calls asking for Sam end at the
FAQ instead of at `Warm Transfer to Sam`, and "which node did she end up in" is the only record of
that. Note the asymmetry - `@Warm Transfer` in the forbid list is reliable, because never reaching a
node cannot be masked by the playground's inability to bridge a call. Going the other way, a script
that must transfer proves it the way §7.3 does: let the transfer fail and assert `log_callback`.

Note on the transport: the playground is stateless, so the whole history is resent each turn - and
it has to include the `node_transition` and tool messages the API returned, not just the words. Echo
back only the dialogue and the flow restarts at the greeting on every turn, which looks like the
agent failing to reach any function node at all. Echo everything and the conversation walks the flow
properly: Greeting -> Collect Callback Details -> Log Callback. `transfer_call` cannot complete on a
web session (Retell transfers are phone-only), so scripts here stop before a bridge and the transfer
paths stay in §7 where a real handset can test them.

    python3 tests/test_calls.py            # every script
    python3 tests/test_calls.py --show     # print the whole conversation
    python3 tests/test_calls.py -k refused # scripts whose label matches
"""
import json, os, sys, urllib.request

KEY = os.environ['RETELL_API_KEY']
AGENT = '' + CLIENT.get('_agent_id','') + ''
BASE = 'https://api.retellai.com'

DV = {'shop_name': 'Pilot Auto',
      'shop_hours': 'every day except Sunday, 9:00 AM to 6:00 PM',
      'shop_phone': '(555) 010-0000', 'shop_address': '123 Example Street',
      'mo_cell': '+15550100001', 'customer_name': '', 'vehicle': '', 'service': '',
      'current_datetime': ''}

IGNORE = {'get_current_datetime', 'extract_dynamic_variables'}

# Stage directions the caller must never hear. Seen live on 08-04 ("Routing to the general question
# node.") and again on 08-06 ("(No reply — routing straight to transfer prep node.)"). The model is
# narrating the flow instead of walking it, and a caller hears the machinery.
#
# 'empty' earns its place: patch30's FIRST attempt at the fix told her that on a silent transition
# "your reply for that turn is EMPTY", and she promptly said the word out loud. Any instruction
# shaped "your reply is X" hands the model an X to emit. The word stays on this list so the same
# class of fix can never be shipped twice.
LEAKS = ['routing to', 'routing straight', 'transfer prep node', 'the caller wants',
         'no reply —', 'no reply -', ' node.', 'node)', 'empty']

# label, what the caller says in order, tools that MUST have fired, tools that must NOT
SCRIPTS = [
    # 08-06 §7.3, verbatim, and the reason this file exists. Asked for Sam, refused to say why,
    # nobody picked up, and the fall-back to a callback collected a number and a time and then
    # ended the call. Collect Callback's only exit demanded a reason that had already been
    # refused two nodes earlier, so it could never match: she kept talking, said Sam would ring at
    # 9 AM, and nothing was written down. The transfer really does fail in the playground, so this
    # walks the whole path.
    ('callback after a failed transfer, reason refused',
     ["Can I talk to Sam, please?", "Bob.", "I'd rather not say.", "This one right here.",
      "Tomorrow at nine AM.", "No, that's it. Thanks."],
     ['log_callback'], []),

    # The same refusal, but reaching the node directly rather than via a failed transfer. This one
    # already worked; it is here so the fix cannot be a special case that only helps one route.
    ('callback, reason refused',
     ["Can you have Sam call me back?", "It's Bob Jones.", "This one right here.",
      "I'd rather not say."],
     ['log_callback'], []),

    # The ordinary one. This has always worked; it is here so a fix to the case above cannot break
    # the case below it.
    ('callback, everything given',
     ["Can you have Sam call me back?", "It's Bob Jones.", "This one right here.",
      "It's about my brakes.", "Any time tomorrow morning."],
     ['log_callback'], []),

    # A caller who will not give a name still has to end up on the owner's list. "Caller" is the agreed
    # fallback; what must never happen is her promising a callback and logging nothing.
    ('callback, no name given',
     ["I need Sam to call me back.", "I'd rather not give my name.", "Use this number.",
      "It's about an oil change."],
     ['log_callback'], []),

    # Booking must still reach the calendar in one clean run — the counterweight to the three
    # above, so that loosening the callback path cannot quietly turn bookings into narration.
    #
    # 08-06: this script used to ask for an "Oil change", which patch27 taught her to refuse. That
    # inverted it — it passed only on the runs where she wrongly booked one, so a green suite was
    # evidence of the bug rather than its absence. The service is now one the shop actually does.
    ('booking reaches the calendar',
     ["I'd like to book an appointment.", "Bob Jones.", "This number here.",
      "2012 Hyundai Veloster.", "Brakes are squealing.", "Tomorrow at 10 AM.",
      "Yes, that's right."],
     # '@Booking Confirmed' and not just the tool call. On 08-10 this script passed green while
     # every booking on the live number was silently failing: log_appointment fired, the backend
     # answered with nothing, and the assertion "the tool ran" was satisfied. Asserting the tool
     # ran proves she tried. Asserting where she LANDED proves it worked.
     ['log_appointment', '@Booking Confirmed'], []),

    # The other half of the split above, and it INVERTED on 08-11. Until the owner's onboarding an oil
    # change was a flat no and this script proved she held it. His actual rule is: not standalone,
    # but yes alongside other work. So the refusal still has to hold for the caller who wants
    # ONLY an oil change - he names a day, gives a time, says "yes that's right", three chances to
    # cave, and nothing may reach the calendar.
    ('standalone oil change books nothing',
     ["I'd like to book an appointment.", "Bob Jones.", "This number here.",
      "2012 Hyundai Veloster.", "Just an oil change, nothing else.", "Tomorrow at 10 AM.",
      "Yes, that's right."],
     [], ['log_appointment', 'log_callback']),

    # ...and the direction that was WRONG until 08-11: a caller booking real work who wants the oil
    # done while it is in. She was refusing this, which turned away work Sam wants. This script is
    # the reason the fact and the tests had to move in one commit - left alone, the script above
    # would have gone green on a bug rather than a behaviour.
    ('oil change alongside other work does book',
     ["Hi.", "I'd like to book an appointment.", "Bob Jones.", "Yes.", "This number here.",
      "2012 Hyundai Veloster.", "Brakes are squealing, and an oil change while it's in.",
      "Tomorrow at 10 AM.", "Yes, that's right."],
     ['log_appointment', '@Booking Confirmed'], []),

    # An information call must not touch anything. Nothing to write, nothing written.
    ('shop question writes nothing',
     ["What are your hours?", "Thanks, that's all."],
     [], ['log_callback', 'log_appointment', 'cancel_appointment', 'update_appointment']),

    # ---- transfer triage (patch31) -------------------------------------------------------
    # The claim: "Can I speak to Sam?" no longer means the owner's phone rings. She asks what it's about
    # and finishes it herself when it is genuinely hers.
    #
    # These four assert only that the transfer did NOT happen, which is the direction that
    # matters - a triage that quietly stops solving anything still passes a "she answered"
    # check, but a triage that dials Sam anyway is the whole feature failing. That she gave a
    # real answer is confirmed on a handset in §7.8-§7.11, per this file's own thesis: assert on
    # what she DID.
    ('triage: asks for Sam about hours',
     ["Can I speak to Sam, please?", "I just wanted to know what time you close.",
      "Perfect, thanks. That's all."],
     [], ['@Warm Transfer', 'log_callback', 'log_appointment']),

    # The strongest evidence triage works: he asked for the owner and left with an appointment.
    ('triage: asking for Sam turns into a booking',
     ["Hi.", "Can I talk to Sam?", "I want to get my brakes looked at.", "Bob Jones.", "Yes.",
      "This number here.", "2012 Hyundai Veloster.", "Brakes.", "Tomorrow at 10 AM.",
      "Yes, that's right."],
     ['log_appointment', '@Booking Confirmed'], ['@Warm Transfer']),

    # Price is the most common reason people ask for the owner. The standing rule does not bend
    # because they asked for him: Sam has to see the vehicle. She must not quote, and must not
    # dial him on the first ask.
    ('triage: price ask holds the inspection rule',
     ["Is Sam around?", "I want to know what a brake job costs on a Veloster.",
      "Alright, never mind then."],
     [], ['@Warm Transfer']),

    # The second ask ends it. One push and she connects - here the transfer fails, as it always
    # does in the playground, and log_callback is the proof she got that far.
    #
    # These next three are LONG on purpose. The transfer consumes the name and number on the way
    # out, then Collect Callback asks for them again on the way back, so a script sized for a
    # straight callback runs out of caller turns two questions short of log_callback - which reads
    # as "she promised and never logged it" when it is really "the test stopped talking". First
    # draft of all three failed exactly that way. Trailing "no, that's it" lines give her room to
    # finish; extra lines are harmless, a script one line short is a false alarm.
    ('triage: second ask connects',
     ["Can I speak to Sam?", "What time do you close?", "No, I really need to speak to Sam himself.",
      "Bob Jones.", "This number here.", "It's about my brakes.", "Tomorrow morning.",
      "No, that's it. Thanks."],
     ['log_callback'], []),

    # An angry caller is never triaged. No question, no offer to help, straight through - and
    # she must not detour into the FAQ trying to be useful.
    ('triage: angry caller goes straight through',
     ["This is ridiculous, I just want to talk to a HUMAN.", "Bob Jones.", "This number here.",
      "It's about my brakes.", "Tomorrow morning.", "No, that's it. Thanks."],
     ['log_callback'], ['@General Questions']),

    # Nobody explains a bad repair to an assistant.
    ('triage: complaint goes straight through',
     ["I need to speak to Sam.", "You people did my brakes last week and they're worse.",
      "Bob Jones.", "This number here.", "Tomorrow morning.", "No, that's it. Thanks.",
      "Nope, that's everything."],
     ['log_callback'], ['@General Questions']),

    # ---- three-strike screening (patch33) -------------------------------------------------
    # From call_0ee3b8b7 on 08-07: "Can I speak with Moe?" -> "My brakes." -> transfer_call. One
    # vague word was enough, because one question was all she had. These four are that call and
    # its neighbours, turned into scripts.

    # The defect itself. "My brakes" is a topic, not a reason - she has to ask what they want done
    # about them, and the answer is a booking. Clarifying questions are unlimited and cost no
    # strike, so nothing here should ever reach Sam.
    #
    # The leading "Hi." is load-bearing and is NOT padding. The playground sometimes answers the
    # FIRST user turn with the greeting itself, swallowing it — on a bad run this script went
    # "Can I speak with Sam?" -> [Greeting] -> "My brakes." -> [Collect Booking Details], never
    # touching the screening node, and every later answer landed one question out of step. It
    # measured 3/6 that way and the cause was the harness, not the agent: on a real phone she
    # greets before the caller has said anything, so the misalignment cannot happen. Spending one
    # throwaway turn on the greeting makes the script test what it claims to test.
    ('screening: vague reason gets clarified into a booking',
     ["Hi.", "Can I speak with Sam?", "My brakes.", "I'd like to get them looked at.", "Bob Jones.",
      "Yes.", "This number here.", "2012 Hyundai Veloster.", "Brakes.", "Tomorrow at 10 AM.",
      "Yes, that's right."],
     ['log_appointment', '@Booking Confirmed'], ['@Warm Transfer']),

    # Three refusals and she stops asking. The transfer fails in the playground as always, so
    # log_callback is the proof she got there - and it must take THREE refusals, not one.
    ('screening: three refusals reach Sam',
     ["Can I speak with Sam?", "I just want to speak with Sam.", "I already told you, get me Sam.",
      "Bob Jones.", "This number here.", "It's about my brakes.", "Tomorrow morning.",
      "No, that's it. Thanks."],
     ['log_callback'], []),

    # "It's personal" is not a refusal to be counted, it is an answer that ends the screening.
    # She must not ask what kind of personal.
    ('screening: personal matter is not interrogated',
     ["Can I speak to Sam?", "It's personal.", "Bob Jones.", "This number here.",
      "Tomorrow morning.", "No, that's it. Thanks."],
     ['log_callback'], ['@General Questions']),

    # The loop. Sam declined, she started a callback, the caller asked again - and she rang him a
    # second time inside sixty seconds. Exactly ONE transfer_call is allowed per call now, so this
    # asserts the second ask does NOT produce another one.
    ('screening: no second dial after a failed transfer',
     ["Can I speak to Sam?", "It's personal.", "Bob Jones.", "Can I speak to Sam?",
      "This number here.", "Tomorrow morning.", "No, that's it. Thanks."],
     ['log_callback'], []),
]


def send(messages):
    req = urllib.request.Request(
        BASE + '/agent-playground-completion/' + AGENT, method='POST',
        data=json.dumps({'messages': messages, 'dynamic_variables': DV}).encode(),
        headers={'Authorization': 'Bearer ' + KEY, 'Content-Type': 'application/json'})
    out = json.loads(urllib.request.urlopen(req, timeout=120).read())
    return out if isinstance(out, list) else out.get('messages', [])


def reached(nodes, want):
    """Did a '@Name' assertion land? Substring, case-insensitive, so it holds whether the API
    reports node ids or display names."""
    needle = want[1:].strip().lower()
    return any(needle in (n or '').lower() for n in nodes)


def run(lines, show=False):
    history, tools, transcript, said_all, nodes, results = [], [], [], [], [], []
    last_invoked = '?'
    for line in lines:
        history.append({'role': 'user', 'content': line})
        transcript.append('USER : ' + line)
        spoken = []
        returned = send(history)
        # Everything goes back, including node_transition — that is what carries the flow's
        # position between turns.
        history += returned
        for m in returned:
            role = m.get('role')
            if role == 'agent':
                spoken.append(m.get('content', ''))
            elif role == 'tool_call_invocation':
                name = m.get('name')
                last_invoked = name           # every tool, including the IGNOREd ones
                if name not in IGNORE:
                    tools.append(name)
                    transcript.append('   >>> ' + name + ' ' + (m.get('arguments') or '')[:120])
            elif role == 'tool_call_result':
                # Attribute to the last tool actually INVOKED, not the last one we kept. Pairing a
                # result with tools[-1] silently credits an IGNOREd call's answer to whichever
                # writing function ran before it, which is exactly backwards for a check whose job
                # is spotting a writing function that answered with nothing.
                results.append((last_invoked, m.get('content') or ''))
            elif role == 'node_transition':
                nodes.append(str(m.get('new_node_name')))
                transcript.append('   [node] ' + nodes[-1])
        line_said = ' '.join(x for x in spoken if x).strip()
        said_all.append(line_said)
        transcript.append('AGENT: ' + line_said[:300])
    if show:
        print('\n'.join('       ' + t for t in transcript))
    return tools, nodes, results, transcript, ' '.join(said_all).lower()


def main():
    show = '--show' in sys.argv
    only = sys.argv[sys.argv.index('-k') + 1] if '-k' in sys.argv else None
    scripts = [s for s in SCRIPTS if not only or only in s[0]]
    fails = []
    for label, lines, need, forbid in scripts:
        tools, nodes, results, transcript, said = run(lines)
        hit = lambda t: reached(nodes, t) if t.startswith('@') else t in tools
        missing = [t for t in need if not hit(t)]
        banned = [t for t in forbid if hit(t)]
        leaked = [w for w in LEAKS if w in said]
        # Sam is rung at most ONCE per call, on every path, no exceptions. On 08-07 a caller asked
        # for him again after he'd declined and she dialled a second time — he was interrupted
        # twice in sixty seconds by one caller, and nothing would have stopped a third. This is a
        # blanket invariant rather than a per-script flag because there is no call for which
        # ringing him twice is right.
        redialled = tools.count('transfer_call') > 1
        # A writing function that answers with NOTHING must never be followed by a confirmation.
        # 08-10: log_appointment returned '' and she said "your tow and urgent brake inspection are
        # booked for today at 2:00 PM" — twice, by name and vehicle. The tool fired, so every
        # assertion in this file passed; nobody was checking what came back. Silence is a failure,
        # and the caller must be told so.
        CONFIRMED = ('booking confirmed', 'cancellation confirmed', 'reschedule confirmed',
                     'callback confirmed')
        silent_ok = [t for t, r in results
                     if t in ('log_appointment', 'cancel_appointment', 'update_appointment',
                              'log_callback') and not (r or '').strip()]
        confirmed = [n for n in nodes if (n or '').lower() in CONFIRMED]
        lied = bool(silent_ok and confirmed)
        ok = not missing and not banned and not leaked and not redialled and not lied
        why = ''
        if lied:
            why = ('%s returned NOTHING and she still reached %s — she told the caller it was done'
                   % (', '.join(sorted(set(silent_ok))), ', '.join(sorted(set(confirmed)))))
        elif redialled:
            why = ('dialled Sam %d times on one call — he only ever gets rung once'
                   % tools.count('transfer_call'))
        elif leaked:
            why = 'said the quiet part out loud: %s' % ', '.join(repr(w) for w in leaked)
        elif missing:
            why = 'never %s' % ', '.join(
                'reached ' + t[1:] if t.startswith('@')
                else 'called %s — she may have said it was done anyway' % t for t in missing)
        elif banned:
            why = ', '.join(
                'ended up in %s, which this call must never reach' % t[1:]
                if t.startswith('@') else 'called %s on a call that should write nothing' % t
                for t in banned)
        print('%s %-46s %s' % ('PASS' if ok else 'FAIL', label, why))
        if show or not ok:
            print('\n'.join('       ' + t for t in transcript))
            print('       tools fired: %s' % (tools or '(none)'))
            print('       nodes visited: %s' % (nodes or '(none)'))
        if not ok:
            fails.append(label)
    print('\n%d/%d passed' % (len(scripts) - len(fails), len(scripts)))
    if fails:
        print('failed:', ', '.join(fails))
        sys.exit(1)


if __name__ == '__main__':
    main()
