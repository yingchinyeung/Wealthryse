"""Metadata-only useful-output alert; never dispatches or retries work.

Completion and its indexed rows must belong to the same capture and replica.
Late delivery of old evidence, missing rows and conflicting duplicates cannot
establish health. This covers existing risk/queue/benchmark monitor evidence,
not independently reconciled NAV/FX/financial correctness.
"""


def metadata_findings_query():
    return """let Events = ContainerAppConsoleLogs_CL
| where TimeGenerated > ago(2h)
| where ContainerGroupName_s startswith 'ibkrv2stg-metadata-monitor-'
| where Log_s startswith 'METADATA_MONITOR '
| extend Evidence=parse_json(substring(Log_s, 17))
| extend Replica=ContainerGroupName_s, CapturedAt=todatetime(Evidence.captured_at),
         Kind=tostring(Evidence.kind), Index=tolong(Evidence.index);
let Markers = Events
| where Kind == 'complete'
| summarize DeliveredAt=max(TimeGenerated) by Replica, CapturedAt, Log_s
| extend Evidence=parse_json(substring(Log_s, 17))
| summarize Variants=count(), DeliveredAt=max(DeliveredAt), Marker=take_any(Evidence) by Replica, CapturedAt;
let Selected = Markers | sort by CapturedAt desc, DeliveredAt desc | take 2;
let Latest = Selected | sort by CapturedAt desc, DeliveredAt desc | take 1;
let Rows = Events
| where Kind != 'complete'
| join kind=inner (Selected | project Replica, CapturedAt) on Replica, CapturedAt
| extend InvalidIndex=isnull(Index) or gettype(Evidence.index) != 'long'
    or tostring(Index) != tostring(Evidence.index)
| distinct Replica, CapturedAt, Index, Kind, Log_s, InvalidIndex;
let Coverage = Rows
| summarize RowCount=count(), Indices=make_set(Index, 100000), FirstIndex=min(Index), LastIndex=max(Index),
    InvalidRows=countif(InvalidIndex or Index < 0 or Kind !in ('risk','finding','benchmark')) by Replica, CapturedAt
| extend IndexCount=array_length(Indices);
let CheckedCaptures = Selected
| join kind=leftouter Coverage on Replica, CapturedAt
| extend Expected=tolong(Marker.records), RowCount=coalesce(RowCount, 0), IndexCount=coalesce(IndexCount, 0),
    InvalidRows=coalesce(InvalidRows, 0)
| extend Invalid=isnull(CapturedAt) or CapturedAt < ago(75m) or CapturedAt > now()
    or Variants != 1 or tostring(Marker.mode) != 'shadow' or tostring(Marker.access_check) != 'passed'
    or gettype(Marker.inventory_complete) != 'bool' or coalesce(tobool(Marker.inventory_complete), false) != true
    or gettype(Marker.denied_column_checks) != 'long'
    or tolong(Marker.denied_column_checks) != 7 or isnull(tolong(Marker.denied_column_checks))
    or gettype(Marker.records) != 'long' or isnull(Expected)
    or tostring(Expected) != tostring(Marker.records) or Expected < 0 or Expected > 100000
    or RowCount != Expected or IndexCount != Expected or InvalidRows != 0
    or (Expected > 0 and (FirstIndex != 0 or LastIndex != Expected - 1));
let InvalidCapture = CheckedCaptures
| join kind=inner (Latest | project Replica, CapturedAt) on Replica, CapturedAt
| where Invalid
| project CapturedAt, Issue='Stale, incomplete or conflicting operational evidence',
    Subject='', Benchmark='', State='evidence_invalid';
let Findings = Rows
| join kind=inner (CheckedCaptures | where not(Invalid) | project Replica, CapturedAt) on Replica, CapturedAt
| extend Evidence=parse_json(substring(Log_s, 17))
| extend State=tostring(Evidence.value.state)
| where Kind == 'finding' or (Kind == 'benchmark' and
    (State != 'current_for_scheduled_cycle' or tostring(Evidence.value.quality) !in ('pass','warn')))
    or (Kind == 'risk' and State !in ('dates_aligned','dates_aligned_with_quality_warning','not_active'))
| extend Entity=coalesce(tostring(Evidence.value.work_item_id),tostring(Evidence.value.event_id),
    tostring(Evidence.value.benchmark_id),tostring(Evidence.value.subject_id))
| project CapturedAt, Issue='Persistent operational data or workflow issue',
    Subject=tostring(Evidence.value.subject_id), Benchmark=tostring(Evidence.value.benchmark_id), State, Entity
| summarize CaptureDates=make_set(CapturedAt, 2), First=min(CapturedAt), CapturedAt=max(CapturedAt)
    by Issue, Subject, Benchmark, State, Entity
| extend Captures=array_length(CaptureDates)
| where Captures == 2 and CapturedAt - First >= 20m
| project CapturedAt, Issue, Subject, Benchmark, State;
let Missing = Latest | summarize N=count()
| where N == 0
| project CapturedAt=datetime(null), Issue='No operational completion evidence in two hours',
    Subject='', Benchmark='', State='missing_evidence';
let Malformed = Events
| where Kind == 'complete' and isnull(CapturedAt) and TimeGenerated > ago(75m)
| summarize N=count() | where N > 0
| project CapturedAt=datetime(null), Issue='Malformed operational completion evidence',
    Subject='', Benchmark='', State='malformed_evidence';
union InvalidCapture, Findings, Missing, Malformed
| distinct CapturedAt, Issue, Subject, Benchmark, State
| extend Scope='Restricted risk, queue and benchmark metadata only'
| project CapturedAt, Issue, Subject, Benchmark, State, Scope"""
