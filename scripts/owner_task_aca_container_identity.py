"""Inert compatibility adapter for Azure's optional native containerId.

The identifier is descriptive metadata, not an image digest or authority.
Only the observed containerd hexadecimal form is accepted. Original native
responses remain intact; detached validation shadows remove this one field.
All scope, readiness, endpoint and one-event runtime checks remain mandatory.
"""
import re

import owner_task_aca_readiness as prior

validate_job_metadata = prior.validate_job_metadata
validate_execution = prior.validate_execution
ReplicaPending = prior.ReplicaPending


def _shadow(value):
    raw = prior.prior._copy(value, 'native_replica_invalid')
    shadow = prior.prior._copy(raw, 'native_replica_invalid')
    # The predecessor checks every other field, including the exact outer shape.
    if type(shadow) is dict and type(shadow.get('value')) is list:
        for row in shadow['value']:
            if type(row) is not dict or type(row.get('properties')) is not dict: continue
            containers = row['properties'].get('containers')
            if type(containers) is not list: continue
            for container in containers:
                if type(container) is dict and 'containerId' in container:
                    identifier = container['containerId']
                    prior.prior._require(type(identifier) is str and re.fullmatch(
                        r'containerd://(?:[0-9a-f]{32}|[0-9a-f]{64})', identifier) is not None)
                    del container['containerId']
    return raw, shadow


def classify_replica(value, name):
    return prior.classify_replica(_shadow(value)[1], name)


def validate_replica(value, name, *, running=True):
    raw, shadow = _shadow(value)
    prior.validate_replica(shadow, name, running=running)
    return raw['value'][0]


def validate_log_binding(rows, replica, pf):
    checked = validate_replica({'value': [replica]}, replica['name'].rsplit('-', 1)[0])
    shadow = _shadow({'value': [checked]})[1]['value'][0]
    return prior.validate_log_binding(rows, shadow, pf)
