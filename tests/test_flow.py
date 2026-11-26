#!/usr/bin/env python3
# Adapted from the pilot's tests for the template. The originals belong to the live pilot and stay private.
# Reads the rendered client config rather than hardcoded values:
#     python3 tests/test_flow.py clients/<slug>.yml
import json as _json, os as _os, sys as _sys
def _client():
    a=[x for x in _sys.argv[1:] if not x.startswith('-')]
    if not a: _sys.exit('usage: test_flow.py clients/<slug>.yml')
    _sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)),
                                      _os.pardir, 'provision'))
    from render import load_yaml
    cfg = load_yaml(a[0])
    return cfg
CLIENT = _client()
SLUG = CLIENT['slug']
"""Static checks on the deployed Retell conversation flow.

Three fix passes in a row shipped a flow defect that only a live voice call could reveal:

  07-25  transition conditions that asked the LLM to introspect state it cannot see
  07-26  a backend response shape ({needs_lookup:true}) with no edge to match it
  07-26  node_transfer_prep, a conversation node that could only advance if the caller spoke —
         and the caller is silent while waiting for a transfer, so it never did

Every one of those is visible in the flow JSON. This reads the LIVE flow (not the repo
snapshot, which can lag) and asserts the four invariants that would have caught them.

    python3 tests/test_flow.py
"""
import json, os, re, sys, urllib.request

KEY = os.environ['RETELL_API_KEY']
FLOW = '' + CLIENT.get('_flow_id','') + ''
BASE = 'https://api.retellai.com'
AGENT = '' + CLIENT.get('_agent_id','') + ''
PHONE = '+15550100000'

ALWAYS_RE = re.compile(r'^\s*always\b', re.I)
E164_RE = re.compile(r'^\+[1-9]\d{7,14}$')

# Flags W1 can put on a response, and the function node that has to be able to act on each.
# A backend that can answer with a shape no edge matches is the 07-26 Gap 3 failure: the
# caller ends up somewhere plausible and wrong.
EXPECTED_EDGES = {
    'node_fn_log_appointment': ['success', 'conflict', 'outside_clinic_hours', 'error'],
    'node_fn_update_appointment': ['success', 'conflict', 'outside_clinic_hours',
                                   'needs_lookup', 'error'],
    'node_fn_cancel_appointment': ['success', 'needs_lookup', 'error'],
    'node_fn_find': ['found', 'not_found', 'multiple'],
    'node_fn_find_auto': ['found', 'not_found', 'multiple'],
    'node_fn_log_callback': ['success', 'error'],
}

# What each flag looks like in the transition-condition text the LLM is given.
FLAG_WORDS = {
    'success': ['success', 'booked', 'created', 'updated', 'cancelled', 'logged', 'went through'],
    'conflict': ['conflict', 'not available', 'unavailable', 'alternative'],
    'outside_clinic_hours': ['outside', 'closed', 'hours'],
    'needs_lookup': ['needs_lookup', 'look', 'lookup'],
    'error': ['error', 'fail', 'could not', 'did not respond', 'trouble', 'unavailable'],
    'found': ['found: true', 'found true', 'was found', 'an appointment was found', 'found an'],
    'not_found': ['found: false', 'found false', 'no appointment', 'nothing', 'not find',
                  "didn't find", 'no upcoming'],
    'multiple': ['multiple', 'more than one', 'two or more'],
}

results = []


def check(name, ok, detail=''):
    results.append((name, ok, detail))
    print(('PASS ' if ok else 'FAIL ') + name + (' | ' + detail if detail else ''))


def all_edges(node):
    out = list(node.get('edges') or [])
    for k in ('always_edge', 'edge'):
        if node.get(k):
            out.append(node[k])
    return out


def get(path):
    req = urllib.request.Request(BASE + path, headers={'Authorization': 'Bearer ' + KEY})
    return json.load(urllib.request.urlopen(req))


def live_versions():
    """The agent version the PHONE answers with, and the flow version behind it.

    Read the draft and you are testing something no caller can reach. On 08-06 the phone was two
    patches behind the draft and every check here passed anyway, which is how a day of live
    testing got spent on the old flow.
    """
    agent_v = None
    for p in get('/list-phone-numbers'):
        if p['phone_number'] == PHONE:
            for a in (p.get('inbound_agents') or []):
                if a['agent_id'] == AGENT:
                    agent_v = a.get('agent_version')
    flow_v = None
    for v in get('/get-agent-versions/' + AGENT):
        if v.get('version') == agent_v:
            flow_v = (v.get('response_engine') or {}).get('version')
    draft_flow_v = (get('/get-agent/' + AGENT).get('response_engine') or {}).get('version')
    return agent_v, flow_v, draft_flow_v


def main():
    agent_v, flow_v, draft_flow_v = live_versions()
    if flow_v is None:
        sys.exit('ABORT: cannot tell which flow version %s answers with' % PHONE)
    f = get('/get-conversation-flow/%s?version=%s' % (FLOW, flow_v))
    nodes = f['nodes']
    byid = {n['id']: n for n in nodes}
    print('phone %s -> agent v%s -> flow v%s | %d nodes | %d tools\n'
          % (PHONE, agent_v, flow_v, len(nodes), len(f['tools'])))

    # 0. Everything below tests the version on the phone. If the draft has moved on, the patches
    #    sitting in it are not live and publish_live.py has not been run.
    check('the phone is running the newest flow', flow_v == draft_flow_v,
          'clean' if flow_v == draft_flow_v else
          'phone is on flow v%s, draft is v%s - run publish_live.py' % (flow_v, draft_flow_v))

    # 1. Nothing that expects to advance on its own can only advance on a caller turn.
    #    This is call_f58d2f3d: node_transfer_prep spoke its line and then sat there for 50s.
    stalls = []
    for n in nodes:
        if n.get('type') != 'conversation' or n.get('always_edge'):
            continue
        es = n.get('edges') or []
        if len(es) == 1 and ALWAYS_RE.match((es[0].get('transition_condition') or {}).get('prompt', '')):
            stalls.append('%s(%s)' % (n['id'], es[0]['id']))
    check('no conversation node stalls on an "Always" prompt edge',
          not stalls, ', '.join(stalls) or 'clean')

    # 2. Reachability, both directions.
    seen, stack = set(), [f['start_node_id']]
    while stack:
        nid = stack.pop()
        if nid in seen or nid not in byid:
            continue
        seen.add(nid)
        stack += [e['destination_node_id'] for e in all_edges(byid[nid])]
        if byid[nid].get('global_node_setting'):
            pass
    # global nodes are entered from anywhere, so seed them and re-walk
    stack = [n['id'] for n in nodes if n.get('global_node_setting')]
    while stack:
        nid = stack.pop()
        if nid in seen or nid not in byid:
            continue
        seen.add(nid)
        stack += [e['destination_node_id'] for e in all_edges(byid[nid])]
    orphans = sorted(set(byid) - seen)
    # node_end is entered through the global Call Ending node; see NOTES.md
    orphans = [o for o in orphans if o != 'node_end']
    check('every node is reachable', not orphans, ', '.join(orphans) or 'clean')

    dangling = ['%s:%s->%s' % (n['id'], e['id'], e['destination_node_id'])
                for n in nodes for e in all_edges(n) if e['destination_node_id'] not in byid]
    check('no edge points at a missing node', not dangling, ', '.join(dangling) or 'clean')

    # 3. Every response shape the backend can emit has an edge that matches it.
    missing = []
    for nid, flags in EXPECTED_EDGES.items():
        if nid not in byid:
            missing.append('%s: node absent' % nid)
            continue
        text = ' '.join((e.get('transition_condition') or {}).get('prompt', '').lower()
                        for e in all_edges(byid[nid]))
        for flag in flags:
            if not any(w in text for w in FLAG_WORDS[flag]):
                missing.append('%s: no edge for %s' % (nid, flag))
    check('every W1 response flag has a matching edge', not missing,
          '; '.join(missing) or 'clean')

    # 4. The transfer can actually dial.
    xfer = [n for n in nodes if n.get('type') == 'transfer_call']
    ok = len(xfer) == 1 and E164_RE.match(
        (xfer[0].get('transfer_destination') or {}).get('number', '') or '')
    check('transfer destination is a single E.164 number', bool(ok),
          (xfer[0]['transfer_destination']['number'] if xfer else 'no transfer_call node'))

    # Not an assertion — the owner's number being the tester's own line is a testing hazard, not a
    # defect. It cost a whole B-section run on 2026-07-26, so say it out loud.
    if xfer:
        print('\nnote: transfers dial %s — call from a DIFFERENT handset or the line is busy.'
              % xfer[0]['transfer_destination']['number'])

    # 5. No silent conversation node may sit between the caller and a function.
    #    Cancel Router (deleted 08-01) was a "Silent router - do not speak" conversation node
    #    whose edges asked the LLM to introspect whether it held an event_id. The caller waits
    #    for the agent, the agent waits for the caller, the inactivity timer ends the call.
    #    Same shape as node_transfer_prep (07-26). Hub is the one deliberate exception: it is
    #    only ever entered on a caller utterance, which is what re-evaluates its edges.
    #    The hazard is not silence, it is silence with no way out that the AGENT controls. A node
    #    carrying a skip_response_edge or an always_edge advances on its own and cannot deadlock,
    #    however quiet it is — that is the shape node_call_ending always had and the shape
    #    node_transfer_prep lacked. Keyword-matching alone flagged node_global_human_request on
    #    08-08 purely because patch33 told it "do not speak at all" for a calm caller, which is
    #    correct behaviour on a node whose skip_response_edge moves the call along regardless.
    #    Greeting joins Hub on the same ticket: it speaks its opening line unconditionally at the
    #    start of every call, and the "say nothing" in it governs only the branch where a caller
    #    has asked for Sam — a branch reached by the caller speaking, which is what re-evaluates
    #    the edges. Neither node can sit silent waiting for a caller who is waiting for it.
    SILENT_OK = {'Hub', 'Greeting'}
    silent = []
    for n in nodes:
        if n.get('type') != 'conversation':
            continue
        name = n.get('name') or n['id']
        text = ((n.get('instruction') or {}).get('text') or '').lower()
        quiet = 'do not speak' in text or 'silent' in text or 'say nothing' in text
        self_advancing = bool(n.get('skip_response_edge') or n.get('always_edge'))
        if quiet and not self_advancing and name not in SILENT_OK:
            silent.append(name)
    check('no silent conversation node can deadlock a call', not silent,
          ', '.join(silent) or 'clean (Hub whitelisted)')

    # 6. A rule in the prompt must have a path in the graph.
    #    call_3dbcdde1 (08-04): RETRACTION WINS told her to put a cancelled appointment back with
    #    log_appointment, but Cancellation Confirmed's only edge went to Hub. She could not do it,
    #    so she said it instead — a prompt rule with no matching edge turns a missing feature into
    #    a false promise, which is the exact thing NEVER PROMISE PENDING WORK exists to prevent.
    #    Same failure shape as 07-26 Gap 3, one level up: there the backend could say something the
    #    graph had no edge for, here the prompt could.
    gp = (f.get('global_prompt') or '').lower()
    if 'log_appointment' in gp and 'retraction' in gp:
        cc = byid.get('node_cancellation_confirmed')
        reach = cc and any(e['destination_node_id'] == 'node_fn_log_appointment'
                           for e in all_edges(cc))
        check('the prompt-promised restore has an edge to Log Appointment', bool(reach),
              'clean' if reach else
              'RETRACTION WINS promises a re-book that Cancellation Confirmed cannot reach')
    else:
        check('the prompt-promised restore has an edge to Log Appointment', True,
              'skipped — RETRACTION WINS no longer promises a re-book')

    # 7. Nothing may hand off to a silent destination on an "always" edge.
    #    Retell: an ALWAYS edge transitions "as soon as the user responds"; a SKIP RESPONSE edge
    #    transitions "when the agent finishes speaking". After "let me get Sam — connecting you now"
    #    the caller has no reason to say anything, so an always_edge into the transfer node parks
    #    the call until the 40s reminder and the 45s hang-up. That is 08-04's 3.7 and 7.4, and the
    #    mid-call "can I speak to Sam?" that froze after a booking.
    #    Third time for one bug: 07-26 deleted node_transfer_prep for it, 08-01 deleted the Cancel
    #    Router for it. Both removed a node instead of fixing the edge, so it grew back in four
    #    more places. node_call_ending had the right shape (skip_response_edge) the whole time.
    SILENT_DEST = {'transfer_call', 'end'}
    stalled = []
    for n in nodes:
        ae = n.get('always_edge')
        if not ae:
            continue
        dest = byid.get(ae.get('destination_node_id') or '')
        if dest and dest.get('type') in SILENT_DEST:
            stalled.append('%s -> %s' % (n['id'], dest.get('name') or dest['id']))
    check('no always_edge hands off to a node the caller will not speak into',
          not stalled, '; '.join(stalled) or 'clean')

    # 8. She must have a clock that a switched-off workflow cannot take away.
    #    08-05 21:20: get_current_datetime 404'd because the Appointment Logger was off for the
    #    §3.7 test, and the prompt header read "Current date and time: {{current_datetime}}" —
    #    a dynamic variable nothing ever fills. With no date anywhere she used the model's
    #    training-time date: "next Thursday is July 4th", in August, and sent preferred_date
    #    2024-07-04. Retell's built-ins are substituted before the model runs and cannot fail.
    gp_raw = f.get('global_prompt') or ''
    has_clock = '{{current_time_America/Winnipeg}}' in gp_raw
    has_cal = '{{current_calendar_America/Winnipeg}}' in gp_raw
    check('the date comes from a source that cannot 404', has_clock and has_cal,
          'clean' if (has_clock and has_cal) else
          'header is missing %s' % (', '.join(
              n for n, ok in (('current_time', has_clock), ('current_calendar', has_cal)) if not ok)))

    # 9. Triage must never stand between a FAILED booking and Sam.
    #    patch31 turned node_transfer_prep2 into a node that tries to solve the caller's problem
    #    before connecting them. That is right for someone who rang up asking for the owner, and
    #    catastrophic for §3.7: the booking has just blown up, she is escalating a system failure,
    #    and there is nothing for her to "solve". Those three nodes reach node_transfer_call by a
    #    skip_response_edge, bypassing prep entirely — which is the only reason patch31 needed no
    #    rule about failed bookings. Nothing in the flow says it has to stay that way, so this does.
    failure_nodes = ['node_booking_failed', 'node_reschedule_failed', 'node_cancellation_failed']
    detoured = []
    for nid in failure_nodes:
        n = byid.get(nid)
        if not n:
            detoured.append('%s is missing' % nid)
            continue
        dests = {e.get('destination_node_id') for e in all_edges(n)}
        if n.get('skip_response_edge'):
            dests.add(n['skip_response_edge'].get('destination_node_id'))
        if 'node_transfer_prep2' in dests or 'node_transfer_call' not in dests:
            detoured.append('%s -> %s' % (nid, ', '.join(sorted(d for d in dests if d))))
    check('a failed booking still goes straight to Sam, never through triage',
          not detoured, '; '.join(detoured) or 'clean')

    # 10. Triage's escape hatches have to exist, or "she tries to solve it first" is a node that
    #     asks a question it can do nothing with — the caller answers "what are your hours" and
    #     the only edge out is the one that dials Sam anyway.
    prep = byid.get('node_transfer_prep2')
    want_edges = {'e_prep_xfer': 'node_transfer_call',
                  'e_prep_faq': 'node_faq',
                  'e_prep_book': 'node_collect_booking',
                  'e_prep_reschedule': 'node_reschedule_main',
                  'e_prep_cancel': 'node_anything_else',
                  'e_prep_callback': 'node_collect_callback'}
    got = {e['id']: e.get('destination_node_id') for e in all_edges(prep or {})}
    wrong = ['%s -> %s (want %s)' % (k, got.get(k, 'MISSING'), v)
             for k, v in want_edges.items() if got.get(k) != v]
    check('triage can route to every place it might have to solve something',
          not wrong, '; '.join(wrong) or '%d edges, all correct' % len(want_edges))

    # 11. Cancelling from triage must land in the Hub, not on the cancel function. The Hub owns
    #     the confirm-once rule; wiring around it deletes an appointment on the first mention of
    #     the word "cancel".
    check('triage cannot cancel an appointment without the confirmation step',
          got.get('e_prep_cancel') != 'node_fn_cancel_appointment',
          'goes to %s' % got.get('e_prep_cancel'))

    # 12. Sam must not be reachable twice on one call.
    #     08-07, call_0ee3b8b7: he declined, she began a callback, the caller asked for him again,
    #     and Human Request Detection — a GLOBAL node, so it fires from inside the callback it just
    #     started — sent them back round to another transfer_call. He was interrupted twice in
    #     sixty seconds and nothing would have stopped a third. The guard lives in the global
    #     node's condition because Retell reports a decline and a genuine failure identically
    #     ("Transfer failed due to an error."), so no edge can branch on which one happened.
    human = byid.get('node_global_human_request')
    cond = ((human or {}).get('global_node_setting') or {}).get('condition') or ''
    guarded = 'ALREADY been dialled' in cond
    check('the human-request node will not ring Sam a second time', guarded,
          'clean' if guarded else 'the already-dialled guard is gone from the global condition')

    # 13. Silence from the backend must reach a failure edge.
    #     08-10, call_7c9b1cd1: log_appointment returned '' — an empty string, no fields at all —
    #     and she told the caller twice that his brakes were booked for 2 PM. Every edge out of a
    #     function node describes a FIELD IN A RESPONSE ("success: true", "success: false without
    #     conflict"), so a response with no fields matches none of them, including the error one.
    #     Given four conditions and an answer satisfying none, the model picks whichever suits the
    #     conversation, and the conversation was a man who wanted an appointment.
    #     patch35 makes each failure edge own the empty case explicitly. This keeps it owned.
    FAIL_EDGE = {'node_fn_log_appointment': 'node_booking_failed',
                 'node_fn_cancel_appointment': 'node_cancellation_failed',
                 'node_fn_update_appointment': 'node_reschedule_failed',
                 'node_fn_log_callback': 'node_callback_failed'}
    unguarded = []
    for nid, dest in FAIL_EDGE.items():
        n = byid.get(nid)
        e = next((x for x in ((n or {}).get('edges') or [])
                  if x.get('destination_node_id') == dest), None)
        cond = ((e or {}).get('transition_condition') or {}).get('prompt') or ''
        if 'SILENCE IS A FAILURE' not in cond:
            unguarded.append(nid)
    check('an empty response from the backend counts as a failure, not a success',
          not unguarded, ', '.join(unguarded) or 'all 4 writing functions guarded')

    fails = [r for r in results if not r[1]]
    print('\n%d/%d passed' % (len(results) - len(fails), len(results)))
    if fails:
        sys.exit(1)


if __name__ == '__main__':
    main()
