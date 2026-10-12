"""Continue the frozen rehearsal, accounting for observed read-only ARM defaults."""
import copy
from pathlib import Path

import rehearse_ms_website_release_rollback as original


def non_image(job):
    result=original.clean_reference(copy.deepcopy(job))
    # ARM adds these two read-only identifiers to the same assigned identity.
    # Identity resource keys, identity type, registry binding and env stay exact.
    for identity in result.get('identity',{}).get('userAssignedIdentities',{}).values():
        identity.pop('clientId',None)
        identity.pop('principalId',None)
    config=result['properties']['configuration']
    if config.get('secrets')==[]:
        config.pop('secrets')
    return original.old.settings_invariant(result)


def main():
    plan=original.read('plan');original.p.verify_protected(plan)
    original.require(plan['controller_sha256']==original.p.sha(Path(original.__file__)),
                     'Original frozen controller changed')
    name='projection-continuation'
    target=original.STAGE/(name+'.json')
    if not target.exists():
        record={'controller_sha256':original.p.sha(Path(__file__)),
                'original_plan_sha256':original.p.sha(original.STAGE/'plan.json'),
                'observed_defaults':['identity assigned resource clientId/principalId',
                                     'empty configuration.secrets omitted'],
                'live_mutations':0,'database_connections':0}
        original.p.release.build.save(original.STAGE,name,{**record,'protected_receipt':
            original.p.retain(record,'ms-website-rollback-projection-continuation-v1')})
    proof=original.read(name);original.p.verify_protected(proof)
    original.require(proof['controller_sha256']==original.p.sha(Path(__file__))
        and proof['original_plan_sha256']==original.p.sha(original.STAGE/'plan.json'),
        'Frozen continuation changed')
    original.non_image=non_image
    original.main()


if __name__=='__main__':main()
