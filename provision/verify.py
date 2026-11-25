#!/usr/bin/env python3
"""Is this client actually ready for a customer to ring?

    python3 provision/verify.py clients/bobsgarage.yml

Checks the live systems, not the rendered files — the gap between those two is where every
outage in this project has lived. Reads only; changes nothing.

Ordered so the first failure is the most important one. A red line here is a customer hearing
silence, not a style complaint.
"""
import json, os, sys, urllib.request, urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, os.pardir)
sys.path.insert(0, HERE)
from render import load_yaml, get      # noqa: E402

N8N = 'https://automa1ons.app.n8n.cloud/api/v1'
RETELL = 'https://api.retellai.com'
results = []


def check(name, ok, detail=''):
    results.append(ok)
    print('  [%s] %-52s %s' % ('x' if ok else ' ', name, detail))


def api(base, path, key, bearer=False):
    h = {'Authorization': 'Bearer ' + key} if bearer else {'X-N8N-API-KEY': key}
    return json.load(urllib.request.urlopen(urllib.request.Request(base + path, headers=h),
                                            timeout=25))


def main():
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    if not args:
        sys.exit('usage: verify.py clients/<slug>.yml')
    cfg = load_yaml(args[0])
    slug = cfg['slug']
    nk, rk = os.environ.get('N8N_API_KEY'), os.environ.get('RETELL_API_KEY')
    if not (nk and rk):
        sys.exit('N8N_API_KEY and RETELL_API_KEY must be set.')

    dep_path = os.path.join(ROOT, 'build', slug, 'deployed.json')
    if not os.path.exists(dep_path):
        sys.exit('no deployed.json for %s — has deploy.py run?' % slug)
    dep = json.load(open(dep_path))

    print('%s — %s\n' % (slug, get(cfg, 'business.name')))

    # 1. Workflows exist AND are active. An inactive workflow is a webhook that 404s, which
    #    presents to the caller as her saying she booked something that never happened.
    live = {w['name']: w for w in (api(N8N, '/workflows?limit=250', nk).get('data') or [])}
    for label, wid, name in dep['workflows']:
        w = live.get(name)
        check('workflow %s' % label, bool(w) and w.get('active'),
              'active' if (w and w.get('active')) else ('INACTIVE' if w else 'MISSING'))

    # 2. The agent exists and a phone number points at it. Without this, she answers nothing.
    try:
        agent = api(RETELL, '/get-agent/' + dep['agent_id'], rk, bearer=True)
        check('retell agent exists', True, 'v%s' % agent.get('version'))
    except urllib.error.HTTPError:
        check('retell agent exists', False, dep['agent_id'])
        agent = {}

    bound = False
    try:
        num = api(RETELL, '/get-phone-number/' + get(cfg, 'phone.makati_line'), rk, bearer=True)
        bound = any(a.get('agent_id') == dep['agent_id']
                    for a in (num.get('inbound_agents') or []))
    except urllib.error.HTTPError:
        pass
    check('phone %s answers with this agent' % get(cfg, 'phone.makati_line'), bound,
          '' if bound else 'NOBODY CAN REACH HER UNTIL THIS IS TRUE')

    # 3. Retention. She promises it on every call; a stated period with nothing behind it is
    #    worse than saying nothing.
    want = int(get(cfg, 'compliance.recording_retention_days'))
    got = agent.get('data_storage_retention_days')
    check('recording retention = %d days' % want, got == want, 'live: %s' % got)

    # 4. The transfer loop. Config-level, but it is the one that silently eats every transfer.
    check('transfer target is not the Makati line',
          get(cfg, 'phone.transfer_to') != get(cfg, 'phone.makati_line'))

    # 5. The HMAC secret. The Retell API key doubles as the webhook signing secret, so a rotation
    #    that missed the n8n credential rejects every call with a 401 and announces nothing.
    import hmac, hashlib, time
    url = 'https://automa1ons.app.n8n.cloud/webhook/%s-appointment' % slug
    body = json.dumps({'name': 'get_current_datetime', 'args': {},
                       'call': {'call_id': 'verify'}}, separators=(',', ':'))
    ts = str(int(time.time() * 1000))
    sig = hmac.new(rk.encode(), (body + ts).encode(), hashlib.sha256).hexdigest()
    req = urllib.request.Request(url, data=body.encode(), method='POST', headers={
        'Content-Type': 'application/json', 'x-retell-signature': 'v=%s,d=%s' % (ts, sig)})
    try:
        urllib.request.urlopen(req, timeout=40)
        check('webhook accepts a Retell-signed call', True, 'HMAC secret matches')
    except urllib.error.HTTPError as e:
        check('webhook accepts a Retell-signed call', False,
              'HTTP %s — the n8n credential holding the Retell key does not match the live key. '
              'EVERY booking is being rejected.' % e.code)

    ok = sum(1 for r in results if r)
    print('\n%d/%d' % (ok, len(results)))
    if ok < len(results):
        print('\nNot ready. Each unchecked line above is something a caller would hit.')
        return 1
    print('\nReady for the live calls — see ONBOARDING.md, "Go-live, in order".')
    print('The call forward is the LAST step, not the first.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
