"""Disabled interactive observation gateway; not mounted on the existing website.

One trusted server binding selects one specialist principal. A bearer token
authenticates the user/frontend, NOT the specialist service identity. Background
service authentication and durable delegation are separate, still-required work.
"""
import json
import logging
import time
import uuid
from contextlib import nullcontext
from dataclasses import dataclass
from .agent_gateway_auth import GatewayAuthenticationError,VerifiedUser,canonical_uuid,strict_json
from .agent_observation import Caller,ObservationDenied,_arguments,_guard_database
from .agent_gateway_limits import AdmissionDenied

LOGGER=logging.getLogger('uvicorn.error.ibkr_gateway')
MAX_BODY=32768


@dataclass(frozen=True)
class GatewayBinding:
    tenant_id: str
    agent_principal_id: str

    def __post_init__(self):
        canonical_uuid(self.tenant_id);canonical_uuid(self.agent_principal_id)


class GatewayDenied(RuntimeError):
    def __init__(self,code,status=403):
        super().__init__(code);self.code=code;self.status=status


class DatabaseUserDirectory:
    def __init__(self,connection_factory): self._connections=connection_factory
    def resolve(self,tenant_id,object_id):
        with self._connections(tenant_id) as c:
            _guard_database(c,tenant_id)
            # oid comes from signature-validated claims. Email comes from our
            # current database, never from mutable token email/username claims.
            rows=c.execute('''SELECT entra_object_id,email FROM app_users
                WHERE tenant_id=? AND lower(entra_object_id)=? AND status='active' ''',
                (tenant_id,object_id)).fetchall()
            if len(rows)!=1: return None
            row=rows[0]
            if row['entra_object_id'].lower()!=object_id: return None
            email=row['email']
            if not isinstance(email,str) or email!=email.strip() or not 3<=len(email)<=320 or any(ord(x)<32 for x in email):
                return None
            return email.lower()


class ObservationGateway:
    def __init__(self,*,binding,authenticator,directory,broker,enabled=False,clock=time.time,admission=None,supported_tools=None):
        self.binding=binding;self.authenticator=authenticator;self.directory=directory
        self.broker=broker;self.enabled=enabled;self.clock=clock
        self.admission=admission
        self.supported_tools=supported_tools

    def invoke(self,authorization,body):
        code='gateway_disabled';status='denied';correlation=str(uuid.uuid4())
        try:
            if self.enabled is not True: raise GatewayDenied(code,503)
            user=self.authenticator.authenticate(authorization)
            if not isinstance(user,VerifiedUser) or self.clock()>=user.expires_at:
                raise GatewayDenied('invalid_access_token',401)
            if not isinstance(body,bytes) or len(body)>MAX_BODY: raise GatewayDenied('request_size_limit',413)
            try:
                request=strict_json(body)
                if not isinstance(request,dict) or set(request)!={'tool_name','arguments'}:
                    raise ValueError('Exact request schema required')
                arguments=_arguments(request['tool_name'],request['arguments'])
            except (ValueError,TypeError,RecursionError,ObservationDenied):
                raise GatewayDenied('invalid_observation_request',400) from None
            if self.supported_tools is not None and request['tool_name'] not in self.supported_tools:
                raise GatewayDenied('tool_not_deployed',400)
            with self.admission.admit(user.object_id) if self.admission else nullcontext():
                email=self.directory.resolve(self.binding.tenant_id,user.object_id)
                if not email: raise GatewayDenied('user_not_authorized')
                caller=Caller(self.binding.tenant_id,self.binding.agent_principal_id,user.object_id,email)
                result=self.broker.observe(caller,request['tool_name'],arguments)
                if self.clock()>=user.expires_at:
                    raise GatewayDenied('access_token_expired_during_observation',401)
            status='completed';code=None
            return result
        except GatewayAuthenticationError as error:
            code='signing_keys_unavailable' if str(error)=='signing_keys_unavailable' else 'invalid_access_token'
            raise GatewayDenied(code,503 if code=='signing_keys_unavailable' else 401) from None
        except GatewayDenied as error:
            code=error.code;raise
        except AdmissionDenied as error:
            code=str(error);raise GatewayDenied(code,429) from None
        except ObservationDenied:
            code='observation_denied';raise GatewayDenied(code) from None
        except Exception:
            code='gateway_unavailable';status='failed';raise GatewayDenied(code,503) from None
        finally:
            LOGGER.info('SPECIALIST_GATEWAY %s',json.dumps(dict(correlation_id=correlation,status=status,code=code)))


def create_gateway_app(gateway):
    """Explicit factory only: no global app, automatic deployment, or fallback."""
    from fastapi import FastAPI,Request
    from fastapi.responses import JSONResponse
    from starlette.concurrency import run_in_threadpool
    app=FastAPI(docs_url=None,redoc_url=None,openapi_url=None)
    @app.post('/v1/observations')
    async def observe(request:Request):
        try:
            headers=request.headers.getlist('authorization')
            if len(headers)!=1: raise GatewayDenied('single_bearer_required',401)
            if request.headers.get('content-type','').split(';')[0].strip()!='application/json':
                raise GatewayDenied('json_required',415)
            data=bytearray()
            async for chunk in request.stream():
                if len(data)+len(chunk)>MAX_BODY: raise GatewayDenied('request_size_limit',413)
                data.extend(chunk)
            result=await run_in_threadpool(gateway.invoke,headers[0],bytes(data))
            return JSONResponse(result,headers={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'})
        except GatewayDenied as error:
            return JSONResponse({'detail':{'code':error.code}},status_code=error.status,headers={'Cache-Control':'no-store'})
        except Exception:
            return JSONResponse({'detail':{'code':'gateway_unavailable'}},status_code=503,headers={'Cache-Control':'no-store'})
    return app
