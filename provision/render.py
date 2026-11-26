#!/usr/bin/env python3
"""client.yml + core templates -> concrete artifacts for one shop.

Reads a client config, checks every required field is present, and writes the flow, prompts,
workflows and knowledge base for that client into `build/<slug>/`. Writes nothing to Retell or n8n
— `deploy.py` does that, and keeping the two apart means you can read exactly what is about to be
deployed before anything leaves the machine.

    python3 provision/render.py clients/bobsgarage.yml
    python3 provision/render.py clients/bobsgarage.yml --check     # validate only

WHY IT REFUSES RATHER THAN DEFAULTS. Every missing field is a fact nobody established about a real
business. A default here becomes something Makati says to a customer with total confidence — the
exact failure the knowledge base exists to prevent. So a missing field is an error naming the field,
never a fallback.

The one deliberate exception is `knowledge:` — a null there means "not answered yet", which is
valid and expected. Those become refusals plus a callback, which is the correct behaviour for a
fact nobody has confirmed.
"""
import json, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, os.pardir)
CORE = os.path.join(ROOT, 'core')

# Every field render.py will not proceed without, as dotted paths.
REQUIRED = [
    'slug',
    # Both were hardcoded to the pilot's own instance until 11-26, so render.py produced a flow
    # that pointed at my n8n workspace no matter whose config it read. A client deployed from it
    # would have had every booking POST to a host they do not own.
    'infra.n8n_host', 'infra.screener_agent_id',
    'business.name', 'business.owner_name', 'business.address', 'business.city',
    'business.timezone',
    # The spoken name and what kind of business this is. Both reach a caller in the first
    # sentence, and both were hardcoded until 08-18: the prompt called every client "an auto
    # repair shop", which is how the pilot dealership came to tell a seller it does not do sales.
    'business.assistant_name', 'business.kind',
    'hours.spoken', 'hours.open', 'hours.close', 'hours.closed_days',
    'hours.last_appointment', 'hours.appointment_minutes',
    'services.offered',
    'phone.makati_line', 'phone.owner_cell', 'phone.transfer_to', 'phone.sms_from',
    'phone.sms_allowed',
    'google.calendar_id', 'google.spreadsheet_name',
    'email.owner', 'email.alerts',
    'compliance.recording_retention_days',
]


def load_yaml(path):
    """A small YAML reader for the subset client.yml uses.

    Deliberately not PyYAML: this has to run in whatever container an agent happens to be in, and a
    provisioning step that dies on a missing dependency is a provisioning step that gets skipped.
    The subset is nested maps, lists of scalars, and lists of maps — which is all client.yml needs.
    """
    root = {}
    # (indent, container, key-that-owns-it). A key with no inline value is a map until a "- "
    # line proves it is a list, at which point it is converted in place.
    stack = [(-1, root, None)]
    for raw in open(path, encoding='utf-8'):
        line = raw.split(' #')[0].rstrip()
        if not line.strip() or line.strip().startswith('#'):
            continue
        indent = len(line) - len(line.lstrip())
        text = line.strip()
        while len(stack) > 1 and indent <= stack[-1][0]:
            stack.pop()
        pindent, parent, pkey = stack[-1]

        if text.startswith('- '):
            if isinstance(parent, dict):
                # the owning key is a list, not a map - convert
                grand = stack[-2][1] if len(stack) > 1 else root
                if pkey is not None and isinstance(grand, dict) and grand.get(pkey) == parent:
                    parent = []
                    grand[pkey] = parent
                    stack[-1] = (pindent, parent, pkey)
                else:
                    raise ValueError('list item with no owning key: %s' % text)
            item = text[2:].strip()
            if ':' in item and not item.startswith(('"', "'")):
                k, _, v = item.partition(':')
                d = {k.strip(): scalar(v.strip())}
                parent.append(d)
                stack.append((indent, d, None))
            else:
                parent.append(scalar(item))
            continue

        if not isinstance(parent, dict):
            raise ValueError('key inside a list without a "- ": %s' % text)
        k, _, v = text.partition(':')
        k, v = k.strip(), v.strip()
        if v == '':
            nxt = {}
            parent[k] = nxt
            stack.append((indent, nxt, k))
        elif v.startswith('['):
            parent[k] = [scalar(x.strip()) for x in v[1:-1].split(',') if x.strip()]
        else:
            parent[k] = scalar(v)
    return root


def prune(o):
    """An empty dict created for a key that turned out to hold a list becomes that list."""
    if isinstance(o, dict):
        return {k: prune(v) for k, v in o.items()}
    return o


def scalar(v):
    if v.startswith(('"', "'")) and v.endswith(('"', "'")) and len(v) > 1:
        return v[1:-1]
    if v in ('null', '~', ''):
        return None
    if v in ('true', 'false'):
        return v == 'true'
    if re.fullmatch(r'-?\d+', v):
        return int(v)
    return v


def get(cfg, dotted, default='__RAISE__'):
    cur = cfg
    for part in dotted.split('.'):
        if not isinstance(cur, dict) or part not in cur:
            if default == '__RAISE__':
                return None
            return default
        cur = cur[part]
    return cur


def check(cfg, path):
    missing = [f for f in REQUIRED if get(cfg, f) in (None, '', [], {})]
    if missing:
        print('REFUSING TO RENDER — %d field(s) not established for this business:\n' % len(missing))
        for f in missing:
            print('   %s' % f)
        print('\nEach of these is a fact about a real shop. A default would become something Makati')
        print('states to a customer as certain. Go back to the interview — see ONBOARDING.md.')
        sys.exit(1)

    slug = cfg['slug']
    if not re.fullmatch(r'[a-z0-9]+', slug):
        sys.exit('slug must be lowercase letters and digits only (got %r). It becomes a webhook '
                 'path and a workflow name.' % slug)

    # The loop that eats a shop's phone line.
    if get(cfg, 'phone.transfer_to') == get(cfg, 'phone.makati_line'):
        sys.exit('phone.transfer_to is the same as phone.makati_line. Makati would dial herself: '
                 'the caller hears hold music until the timeout and lands on a callback, every '
                 'single transfer.')

    # A cap that lets a job start too late to finish is the bug the pilot shop's system shipped with.
    o, c, la = get(cfg, 'hours.open'), get(cfg, 'hours.close'), get(cfg, 'hours.last_appointment')
    mins = get(cfg, 'hours.appointment_minutes')
    def m(t):
        h, _, mm = str(t).partition(':')
        return int(h) * 60 + int(mm or 0)
    if m(la) + int(mins) > m(c):
        sys.exit('hours.last_appointment %s + %s minutes runs past hours.close %s. A job booked at '
                 'the last slot could not be finished. Either move last_appointment earlier or '
                 'shorten appointment_minutes.' % (la, mins, c))
    if m(la) < m(o):
        sys.exit('hours.last_appointment %s is before hours.open %s.' % (la, o))

    for key in ('makati_line', 'owner_cell', 'transfer_to', 'sms_from'):
        v = get(cfg, 'phone.' + key)
        if not re.fullmatch(r'\+\d{11,15}', str(v)):
            sys.exit('phone.%s must be E.164 like +12045551234 (got %r)' % (key, v))

    if get(cfg, 'phone.owner_cell') not in (get(cfg, 'phone.sms_allowed') or []):
        print('NOTE: phone.owner_cell is not in phone.sms_allowed — the owner will not be able to '
              'reply to his own callback digest.\n')
    return cfg


def substitutions(cfg):
    """The complete client-specific vocabulary. Anything not here is core and stays as it is."""
    tz = get(cfg, 'business.timezone')
    cond = get(cfg, 'services.conditional') or {}
    return {
        'CONDITIONAL_SERVICE': cond.get('service') or '',
        # 'a oil change' is the kind of thing a caller notices and a reviewer does not.
        'A_CONDITIONAL_SERVICE': article(cond.get('service') or ''),
        'CONDITIONAL_SERVICE_UPPER': (cond.get('service') or '').upper(),
        'CONDITIONAL_ALONGSIDE': cond.get('alongside_example') or 'the other work',
        'CLOSED_DAYS_SPOKEN': spoken_days(get(cfg, 'hours.closed_days') or []),
        'SLUG': cfg['slug'],
        # WEBHOOK_HOST, not N8N_HOST: apply() matches {{[A-Z_]+}}, so a placeholder with a
        # digit in it is silently never substituted and slips past the leftover-placeholder
        # check as well. The first version of this line shipped {{N8N_HOST}} into the build.
        'WEBHOOK_HOST': get(cfg, 'infra.n8n_host'),
        'SCREENER_AGENT_ID': get(cfg, 'infra.screener_agent_id'),
        'SHOP_NAME': get(cfg, 'business.name'),
        'OWNER_NAME': get(cfg, 'business.owner_name'),
        'SHOP_ADDRESS': get(cfg, 'business.address'),
        'CITY': get(cfg, 'business.city'),
        'TIMEZONE': tz,
        'HOURS_SPOKEN': get(cfg, 'hours.spoken'),
        'OPEN': get(cfg, 'hours.open'),
        'CLOSE': get(cfg, 'hours.close'),
        'LAST_APPOINTMENT': get(cfg, 'hours.last_appointment'),
        'LAST_APPOINTMENT_SPOKEN': spoken_time(get(cfg, 'hours.last_appointment')),
        'CLOSE_SPOKEN': spoken_time(get(cfg, 'hours.close')),
        'APPOINTMENT_MINUTES': str(get(cfg, 'hours.appointment_minutes')),
        'SERVICES_OFFERED': '\n'.join('  • %s' % s for s in (get(cfg, 'services.offered') or [])),
        'MAKATI_LINE': get(cfg, 'phone.makati_line'),
        'MAKATI_LINE_SPOKEN': spoken_phone(get(cfg, 'phone.makati_line')),
        'OWNER_CELL': get(cfg, 'phone.owner_cell'),
        'TRANSFER_TO': get(cfg, 'phone.transfer_to'),
        'SMS_FROM': get(cfg, 'phone.sms_from'),
        'SMS_ALLOWED': json.dumps(get(cfg, 'phone.sms_allowed') or []),
        'CALENDAR_ID': get(cfg, 'google.calendar_id'),
        'CALENDAR_ID_ENC': str(get(cfg, 'google.calendar_id')).replace('@', '%40'),
        'SPREADSHEET_NAME': get(cfg, 'google.spreadsheet_name'),
        'EMAIL_OWNER': get(cfg, 'email.owner'),
        'EMAIL_ALERTS': get(cfg, 'email.alerts'),
        'RETENTION_DAYS': str(get(cfg, 'compliance.recording_retention_days')),
        # The name callers hear. Kept separate from the slug on purpose: `makati` is baked into
        # webhook paths, workflow names and the Drive query, and renaming those 404s every booking
        # while she tells callers she has booked them. patch49 learned this on the pilot's live system.
        'ASSISTANT_NAME': get(cfg, 'business.assistant_name'),
        'BUSINESS_KIND': get(cfg, 'business.kind'),
        # Two conditional blocks. Both exist because a shop that has the thing and a shop that does
        # not need opposite wording, and the wrong one is worse than none.
        'SELLS_VEHICLES': bool(get(cfg, 'business.sells_vehicles')),
        'TOW_PARTNER': bool(get(cfg, 'knowledge.towing')),
        'NO_TOW_PARTNER': not get(cfg, 'knowledge.towing'),
    }


def spoken_time(hhmm):
    h, _, mm = str(hhmm).partition(':')
    h, mm = int(h), int(mm or 0)
    ap = 'PM' if h >= 12 else 'AM'
    hh = h % 12 or 12
    return '%d:%02d %s' % (hh, mm, ap)


def spoken_phone(e164):
    d = re.sub(r'\D', '', str(e164))[-10:]
    return '(%s) %s-%s' % (d[:3], d[3:6], d[6:])


def article(noun):
    if not noun:
        return ''
    return ('an ' if noun[0].lower() in 'aeiou' else 'a ') + noun


def spoken_days(days):
    d = [str(x).capitalize() for x in days]
    if not d:
        return 'no days'
    if len(d) == 1:
        return d[0]
    return ', '.join(d[:-1]) + ' and ' + d[-1]


def blocks(text, subs):
    """Resolve {{#NAME}}...{{/NAME}} sections.

    Kept if subs[NAME] is truthy, removed entirely if not. There is exactly one of these — the
    conditional-service rule, which most shops will not have. It exists because that block is
    hard-won prompt text: several rounds of work went into making her hold the refusal through a
    named day, a time and a "yes that's right". Generating a replacement from a list would throw
    that away, so the proven wording stays and only the service name is substituted.
    """
    def repl(m):
        return m.group(2) if subs.get(m.group(1)) else ''
    return re.sub(r'\{\{#([A-Z_]+)\}\}(.*?)\{\{/\1\}\}', repl, text, flags=re.S)


def apply(text, subs):
    text = blocks(text, subs)
    out = re.sub(r'\{\{([A-Z_]+)\}\}', lambda m: str(subs.get(m.group(1), m.group(0))), text)
    left = set(re.findall(r'\{\{([A-Z_]+)\}\}', out))
    if left:
        sys.exit('template placeholder(s) with no value: %s' % ', '.join(sorted(left)))
    return out


def main():
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    if not args:
        sys.exit(__doc__.strip().split('\n\n')[0] + '\n\nusage: render.py clients/<slug>.yml')
    cfg = check(load_yaml(args[0]), args[0])
    subs = substitutions(cfg)

    if '--check' in sys.argv:
        print('%s — config valid.' % cfg['slug'])
        for k in ('SHOP_NAME', 'OWNER_NAME', 'HOURS_SPOKEN', 'LAST_APPOINTMENT_SPOKEN',
                  'APPOINTMENT_MINUTES', 'MAKATI_LINE', 'TRANSFER_TO'):
            print('   %-24s %s' % (k.lower(), subs[k]))
        unknown = [k for k, v in (cfg.get('knowledge') or {}).items() if v is None]
        if unknown:
            print('\n   %d knowledge field(s) unanswered — she will refuse and take a callback for '
                  'each,\n   which is correct. Fill them in as the owner answers:' % len(unknown))
            print('   ' + ', '.join(unknown))
        return

    out = os.path.join(ROOT, 'build', cfg['slug'])
    os.makedirs(out, exist_ok=True)
    written = []

    # The global prompt is rendered first and then injected into the flow, because Retell stores it
    # as a field ON the flow. Keeping it as a separate template file means a human can read the
    # prompt without wading through 74KB of node JSON — it is the part most worth reading before
    # a client goes live.
    prompt = apply(open(os.path.join(CORE, 'global_prompt.template.txt'), encoding='utf-8').read(),
                   subs)
    subs['GLOBAL_PROMPT'] = json.dumps(prompt)[1:-1]      # escaped, without the outer quotes

    for name in sorted(os.listdir(CORE)):
        src = os.path.join(CORE, name)
        if os.path.isdir(src):
            continue
        dst = os.path.join(out, name.replace('.template', ''))
        open(dst, 'w', encoding='utf-8').write(apply(open(src, encoding='utf-8').read(), subs))
        written.append(os.path.relpath(dst, ROOT))
    kb_out = os.path.join(out, 'kb')
    os.makedirs(kb_out, exist_ok=True)
    for name in sorted(os.listdir(os.path.join(CORE, 'kb'))):
        src = os.path.join(CORE, 'kb', name)
        dst = os.path.join(kb_out, name.replace('.template', ''))
        open(dst, 'w', encoding='utf-8').write(apply(open(src, encoding='utf-8').read(), subs))
        written.append(os.path.relpath(dst, ROOT))
    json.dump(cfg, open(os.path.join(out, 'client.resolved.json'), 'w'), indent=1)

    print('rendered %s -> build/%s' % (args[0], cfg['slug']))
    for w in written:
        print('   ', w)
    print('\nRead the rendered global_prompt.txt before deploying. Then:')
    print('    python3 provision/deploy.py clients/%s.yml' % cfg['slug'])


if __name__ == '__main__':
    main()
