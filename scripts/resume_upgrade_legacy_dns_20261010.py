"""One-time reconciliation of the empty async acknowledgement; no blind retries."""
import control_upgrade_legacy_dns_20261010 as c


def inspect():
    result = []
    for zone in c.ZONES:
        response = c.request('GET', c.zone_id(zone)+'/virtualNetworkLinks'+c.API)
        if response.get('nextLink'):
            raise ValueError('Unexpected pagination')
        links = response['value']
        if links and (len(links) != 1 or links[0]['id'].lower() != c.link_id(zone).lower()
                      or not c.contains_requested(links[0]['properties'], c.body()['properties'])):
            raise ValueError('Unexpected link ownership or configuration')
        result.append(links)
    return result


def main():
    plan = c.protected(c.OUT/'plan.json')
    intent = c.protected(c.OUT/'apply-intent.json')
    qualified = c.protected(c.OUT/'preview-qualified.json')
    if plan['source_hashes'] != c.source_hash() or plan['boundary'] != c.boundary():
        raise ValueError('Frozen source or infrastructure drift')
    if intent['links'] != plan['links'] or intent['body'] != c.body():
        raise ValueError('Original intent differs')
    if qualified.get('passed') is not True or qualified['plan_sha256'] != c.sha(c.OUT/'plan.json'):
        raise ValueError('Qualified original preview required')
    c.idle_all()
    links = inspect()
    if not links[0] or any(links[1:]):
        raise ValueError('Only the observed first-link-only state may be resumed')
    first = links[0][0]['properties']
    if first.get('provisioningState') != 'Succeeded' or first.get('virtualNetworkLinkState') != 'Completed':
        raise ValueError('First link not ready')
    folder = c.OUT/'async-reconciliation-v1'
    if folder.exists():
        raise ValueError('Reconcile earlier continuation; never retry automatically')
    c.save(folder, 'plan', {'original_failure': "KeyError: id after first PUT async acknowledgement",
        'existing_link': links[0][0], 'missing_links': plan['links'][1:],
        'source_sha256': c.sha(c.ROOT/'scripts/resume_upgrade_legacy_dns_20261010.py'),
        'original_plan_sha256': c.sha(c.OUT/'plan.json')})
    for index, zone in enumerate(list(c.ZONES)[1:], 1):
        if c.request('GET', c.link_id(zone)+c.API, missing=True) is not None:
            raise ValueError('Do not overwrite a link')
        c.save(folder, 'intent-'+str(index), {'id': c.link_id(zone), 'body': c.body()})
        ack = c.request('PUT', c.link_id(zone)+c.API, data=c.body())
        c.save(folder, 'ack-'+str(index), {'requested_id': c.link_id(zone),
            'acknowledged_id': ack.get('id'), 'state': ack.get('properties', {}).get('provisioningState'),
            'readback_required': True})
    print('Submitted only three missing links; all four require exact readback before testing')


if __name__ == '__main__':
    main()
