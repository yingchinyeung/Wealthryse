"""Fixed-job readiness adapter for native replicas awaiting container metadata.

An empty native containers array is pending only after the entire replica scope,
timestamp, known state and optional metadata have been checked. It never supplies
running evidence or log ownership. All populated rows use the frozen adapter.
No IO, credentials, cloud request, SQL, model call or dispatch is provided.
"""
import owner_task_aca_native as prior

validate_job_metadata = prior.validate_job_metadata
validate_execution = prior.validate_execution
validate_log_binding = prior.validate_log_binding
ReplicaPending = prior.ReplicaPending


def _container_metadata_pending(value, name):
    name = prior._name(name)
    raw = prior._copy(value, 'native_replica_invalid')
    prior._require(type(raw) is dict and set(raw) <= {'value', 'nextLink'}
        and type(raw.get('value')) is list and len(raw['value']) <= 1
        and (raw.get('nextLink') is None or type(raw['nextLink']) is str and raw['nextLink'] == ''))
    if not raw['value']: return False
    row = raw['value'][0]
    prior._require(type(row) is dict and set(row) == {'id', 'name', 'type', 'properties'})
    props = row['properties']
    prior._require(type(props) is dict)
    if type(props.get('containers')) is not list or props['containers']: return False
    replica = row['name']; prior.endpoint_metadata(name, replica)
    prior._require(row['id'] == prior.JOB_ID + '/executions/' + name + '/replicas/' + replica
        and row['type'] == prior.REPLICA_TYPE)
    required = {'createdTime', 'runningState', 'containers'}
    prior._require(required <= set(props) <= required | {'runningStateDetails', 'initContainers'}
        and type(props['runningState']) is str and props['runningState'] in {'Running', 'NotRunning', 'Unknown'}
        and ('initContainers' not in props or type(props['initContainers']) is list and not props['initContainers']))
    try: prior.oldm.transport.utc(props['createdTime'])
    except Exception: raise prior.NativeResponseError('native_replica_invalid') from None
    if 'runningStateDetails' in props:
        prior._require(prior._text(props['runningStateDetails'], prior.MAX_DETAILS_CHARS))
    return True


def classify_replica(value, name):
    if _container_metadata_pending(value, name): return 'pending'
    return prior.classify_replica(value, name)


def validate_replica(value, name, *, running=True):
    prior._require(type(running) is bool)
    if _container_metadata_pending(value, name): raise ReplicaPending()
    return prior.validate_replica(value, name, running=running)
