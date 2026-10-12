"""Complete all-container execution-family headers around indexed wire reads.

No log-prefix, container, stream, binding or stderr filter hides foreign rows.
All rows must validate as the registered protocol from the captured replica.
Exact header multisets bracket bounded payload queries. This does not exclude
late delivery, silent replacement replicas or platform logging omissions.
"""
from collections import Counter
from dataclasses import dataclass
import hashlib
import time

import requests
from requests.adapters import HTTPAdapter

import read_owner_task_catalog_family as family
import read_owner_task_catalog_logs as reader

require=reader._require


@dataclass(frozen=True)
class CompleteQuery:
    scope: family.CatalogFamilyScope
    def __post_init__(self):
        require(type(self) is CompleteQuery,'logs_query_invalid');family.checked_scope(self.scope)
    @property
    def text(self):
        self.__post_init__();text=family.CatalogFamilyQuery(self.scope).text
        narrow="| where Log_s startswith '"+reader.FAMILY_PREFIX+"'\n"
        require(text.count(narrow)==1,'logs_query_invalid')
        return text.replace(narrow,'')


def validate_response(value,query,*,http_status=200):
    require(type(query) is CompleteQuery,'logs_query_invalid');query.__post_init__()
    return reader.validate_response(value,reader.CatalogQuery(query.scope.replica_scope,'inventory'),http_status=http_status)


class CompleteLogsTransport:
    def __init__(self,credential,session=None):
        self.credential=credential;self.token=None
        self.session=requests.Session() if session is None else session
        if session is None:
            self.session.mount('https://',HTTPAdapter(max_retries=0));self.session.trust_env=False
    def __call__(self,query):
        require(type(query) is CompleteQuery,'logs_query_invalid');query.__post_init__()
        scope=query.scope.replica_scope;response=None
        try:
            if self.token is None or self.token.expires_on<=time.time()+120:
                self.token=self.credential.get_token(reader.AUDIENCE)
            response=self.session.request('POST',reader.ENDPOINT,json=dict(query=query.text,timespan=scope.start_utc+'/'+scope.end_utc),
                headers={'Authorization':'Bearer '+self.token.token},allow_redirects=False,stream=True,timeout=(5,20))
            chunks=[];count=0
            for chunk in response.iter_content(chunk_size=16384):
                require(type(chunk) is bytes,'logs_response_invalid');count+=len(chunk)
                require(count<=reader.MAX_RESPONSE_BYTES,'logs_response_oversized');chunks.append(chunk)
            value=reader._json(b''.join(chunks),'logs_response_invalid')
            validate_response(value,query,http_status=response.status_code);return value
        except Exception: raise reader.CatalogLogsError('logs_transport_failed') from None
        finally:
            if response is not None: response.close()


def inventory(query,scope):
    scope=family.checked_scope(scope);request=CompleteQuery(scope);response=query(request)
    validate_response(response,request)
    return sorted(reader._inventory(lambda _:response,scope.replica_scope),key=reader._row_fingerprint)


def collect_evidence(complete_query,payload_query,scope):
    scope=family.checked_scope(scope)
    first=inventory(complete_query,scope)
    captured=reader.collect_evidence(payload_query,scope.replica_scope)
    second=inventory(complete_query,scope)
    require(Counter(map(reader._row_fingerprint,first))==Counter(map(reader._row_fingerprint,second)),
        'logs_inventory_drift')
    fingerprint=family.inventory_sha256(first)
    require(fingerprint==captured['header_inventory_sha256']==family.inventory_sha256(second),'logs_inventory_drift')
    return dict(captured=captured,inventory_before=first,inventory_after=second,inventory_sha256=fingerprint,
        observed_all_container_console_complete=True,late_delivery_excluded=False,
        platform_replacement_excluded=False,zero_log_replica_absence_verified=False)


def wire_parts(lines,binding,execution_id,replica_receipt):
    """Dynamic create-only evidence values, each bounded under the 2 MiB cap."""
    require(type(lines) is list and 2<=len(lines)<=reader.MAX_ROWS,'logs_record_invalid')
    reader.codec.decode_record(lines,binding)
    result=[]
    for offset in range(0,len(lines),128):
        value=dict(kind='complete_provider_wire_part',index=len(result),offset=offset,lines=lines[offset:offset+128],
            binding={key:getattr(binding,key) for key in reader.codec.BINDING_FIELDS},execution_id=execution_id,
            replica_receipt=replica_receipt)
        require(len(reader.codec.canonical(value))<=reader.MAX_RESPONSE_BYTES,'logs_response_oversized');result.append(value)
    require(len(result)<=128,'logs_record_invalid');return result


def reconstruct(parts,binding,execution_id,replica_receipt):
    require(type(parts) is list and 1<=len(parts)<=128,'logs_record_invalid')
    lines=[]
    for index,part in enumerate(parts):
        require(type(part) is dict and set(part)=={'kind','index','offset','lines','binding','execution_id','replica_receipt'}
            and type(part['index']) is int and type(part['offset']) is int and part['index']==index and part['offset']==len(lines)
            and part['kind']=='complete_provider_wire_part' and part['execution_id']==execution_id
            and part['replica_receipt']==replica_receipt and part['binding']=={key:getattr(binding,key) for key in reader.codec.BINDING_FIELDS}
            and type(part['lines']) is list and 1<=len(part['lines'])<=128
            and (index==len(parts)-1 or len(part['lines'])==128)
            and len(reader.codec.canonical(part))<=reader.MAX_RESPONSE_BYTES,'logs_record_invalid')
        lines.extend(part['lines'])
    require(len(lines)<=reader.MAX_ROWS,'logs_record_invalid');return reader.codec.decode_record(lines,binding)
