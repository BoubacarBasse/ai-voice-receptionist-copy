#!/usr/bin/env python3
"""Push a rendered client to Retell and n8n.

    python3 provision/deploy.py clients/bobsgarage.yml --dry-run    # what would happen
    python3 provision/deploy.py clients/bobsgarage.yml

Needs N8N_API_KEY and RETELL_API_KEY in the environment. See README.md.

WHAT THIS DOES NOT DO, ON PURPOSE:

  - It does not create or fill credentials. n8n credentials hold real secrets, and an API that can
    write them is an API that can leak them. It creates the workflows referencing credentials BY
    NAME and prints exactly which ones a human must connect in the n8n UI. The pilot shop's system was built
    this way and it is the right amount of friction.
  - It does not buy a phone number or point it at the agent. That costs money and binds a real
    line; the operator does it deliberately, and `verify.py` checks it afterwards.
  - It does not publish the flow. Rendered output should be READ before a customer can reach it.

IT REFUSES TO DEPLOY `pilot-shop`. That config exists to prove the template can express a real
shop, not to be deployed — running it would create a second set of workflows pointing at the owner's real
calendar and a second agent competing for his phone number.
"""
import json, os, sys, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, os.pardir)
sys.path.insert(0, HERE)
from render import load_yaml, check, get      # noqa: E402

RETELL = 'https://api.retellai.com'

# Every credential a client's workflows reference. deploy.py creates the workflows; a human
# connects these in the UI. The Retell one is not obvious and is the one people miss:
# W1 and W3 verify Retell's webhook signature using the RETELL API KEY as the HMAC secret.
CREDENTIALS = [
    ('googleCalendarOAuth2Api', 'Google Calendar', 'read and write the shop calendar'),
    ('googleSheetsOAuth2Api', 'Google Sheets', 'the call log and callback queue'),
    ('smtp', 'SMTP', 'booking summaries and failure alerts'),
    ('twilioApi', 'Twilio', 'every outbound text. Use the API Key shape: Account SID AC…, '
                            'API Key SID SK…, API Key Secret'),
    ('httpHeaderAuth', 'Retell API Key', 'header x-retell-secret = the Retell API key. THIS IS THE '
                                         'HMAC SECRET webhooks are verified against — if it does '
                                         'not match the live Retell key, every call 401s and '
                                         'nothing announces it'),
]


def api(base, path, method='GET', body=None, key=None, bearer=False):
    h = {'Content-Type': 'application/json'}
    h['Authorization' if bearer else 'X-N8N-API-KEY'] = ('Bearer ' + key) if bearer else key
    req = urllib.request.Request(base + path, method=method, headers=h,
                                 data=json.dumps(body).encode() if body is not None else None)
    try:
        raw = urllib.request.urlopen(req).read()
        return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        print('HTTP %s %s\n%s' % (e.code, path, e.read()[:500].decode()))
        raise


def main():
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    if not args:
        sys.exit('usage: deploy.py clients/<slug>.yml [--dry-run]')
    dry = '--dry-run' in sys.argv
    cfg = check(load_yaml(args[0]), args[0])
    slug = cfg['slug']
    # From the config, never a constant: this script POSTs eight workflows somewhere. Hardcoded,
    # it would have created every client's workflows in whichever account built the template.
    n8n = 'https://%s/api/v1' % get(cfg, 'infra.n8n_host')

    if slug == 'pilot-shop':
        sys.exit("REFUSING: clients/pilot-shop.yml is a reference config, not a deployable one.\n"
                 "The pilot shop's system is live and maintained privately. Deploying this would create a second\n"
                 "set of workflows against his real calendar and a second agent competing for his\n"
                 "phone number. Rebuild the pilot from its private\n"
                 "snapshot-restore tool, which is the path that was designed for it.")

    build = os.path.join(ROOT, 'build', slug)
    if not os.path.isdir(build):
        sys.exit('nothing rendered for %s. Run:\n    python3 provision/render.py %s' % (slug, args[0]))

    nk, rk = os.environ.get('N8N_API_KEY'), os.environ.get('RETELL_API_KEY')
    if not (nk and rk):
        sys.exit('N8N_API_KEY and RETELL_API_KEY must be set. See README.md — never paste them '
                 'into a file in this repo.')

    workflows = json.load(open(os.path.join(build, 'workflows.json'), encoding='utf-8'))
    flow = json.load(open(os.path.join(build, 'flow.json'), encoding='utf-8'))

    print('%s — %s' % (slug, get(cfg, 'business.name')))
    print('  %d n8n workflows, %d flow nodes' % (len(workflows), len(flow['nodes'])))
    print('  webhook paths: %s-appointment, %s-call-log, %s-callback-reply' % (slug, slug, slug))

    existing = {w['name'] for w in (api(n8n, '/workflows?limit=250', key=nk).get('data') or [])}
    clash = [w['name'] for w in workflows.values() if w['name'] in existing]
    if clash:
        sys.exit('REFUSING: these workflow names already exist on this n8n instance:\n   %s\n'
                 'Deploying would create confusing duplicates. Pick a different slug, or delete '
                 'the previous attempt first.' % '\n   '.join(clash))

    if dry:
        print('\n--dry-run. Would create:')
        for w in workflows.values():
            print('   n8n workflow  %s' % w['name'])
        print('   retell flow   %d nodes, %d tools' % (len(flow['nodes']), len(flow.get('tools') or [])))
        print('\nThen a human connects these credentials in n8n:')
        for _, name, why in CREDENTIALS:
            print('   %-22s %s' % (name, why))
        return

    created = []
    for label, w in workflows.items():
        r = api(n8n, '/workflows', 'POST', w, key=nk)
        created.append((label, r.get('id'), w['name']))
        print('   created %-30s %s' % (w['name'], r.get('id')))

    prompt = open(os.path.join(build, 'global_prompt.txt'), encoding='utf-8').read()
    flow['global_prompt'] = prompt
    fr = api(RETELL, '/create-conversation-flow', 'POST', flow, key=rk, bearer=True)
    fid = fr.get('conversation_flow_id')
    print('   created retell flow %s' % fid)

    ar = api(RETELL, '/create-agent', 'POST', {
        'agent_name': '%s — Makati' % get(cfg, 'business.name'),
        'response_engine': {'type': 'conversation-flow', 'conversation_flow_id': fid},
        'voice_id': '11labs-Adrian',
        'data_storage_setting': 'everything',
        'data_storage_retention_days': int(get(cfg, 'compliance.recording_retention_days')),
    }, key=rk, bearer=True)
    print('   created retell agent %s' % ar.get('agent_id'))

    state = {'slug': slug, 'workflows': created, 'flow_id': fid, 'agent_id': ar.get('agent_id')}
    json.dump(state, open(os.path.join(build, 'deployed.json'), 'w'), indent=1)

    print('\nNOT USABLE YET. A human has to do these, in the n8n UI and the Retell dashboard:\n')
    for _, name, why in CREDENTIALS:
        print('   [ ] connect credential %-22s %s' % (name, why))
    print('   [ ] activate the %d workflows in n8n' % len(created))
    print('   [ ] buy a phone number in Retell and bind it to agent %s' % ar.get('agent_id'))
    print('   [ ] publish the flow once you have READ build/%s/global_prompt.txt' % slug)
    print('\nThen: python3 provision/verify.py %s' % args[0])


if __name__ == '__main__':
    main()
