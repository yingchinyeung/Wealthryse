"""Bounded single-replica admission; no account access or distributed claims.

The deployment must pin one replica/one process while using this limiter.
Buckets contain only principal hashes. Active requests have no waiting queue.
"""
import hashlib
import math
import threading
import time
from contextlib import contextmanager


class AdmissionDenied(RuntimeError):
    pass


class GatewayAdmission:
    def __init__(self,*,concurrency=1,per_minute=6,max_principals=128,clock=time.monotonic):
        for value in (concurrency,per_minute,max_principals):
            if type(value) is not int or not 1<=value<=1024: raise ValueError('Bounded admission controls required')
        self._slots=threading.BoundedSemaphore(concurrency)
        self._lock=threading.Lock();self._buckets={};self._clock=clock
        self._per_minute=per_minute;self._max_principals=max_principals

    @contextmanager
    def admit(self,principal):
        if not isinstance(principal,str) or not principal or len(principal)>256:
            raise AdmissionDenied('invalid_admission_identity')
        key=hashlib.sha256(principal.encode()).hexdigest()
        now=self._clock()
        if not isinstance(now,(int,float)) or isinstance(now,bool) or not math.isfinite(now):
            raise AdmissionDenied('admission_clock_unavailable')
        with self._lock:
            self._buckets={k:v for k,v in self._buckets.items() if now-v[0]<60}
            if key not in self._buckets and len(self._buckets)>=self._max_principals:
                raise AdmissionDenied('admission_capacity_limit')
            start,count=self._buckets.get(key,(now,0))
            if now<start: raise AdmissionDenied('admission_clock_unavailable')
            if count>=self._per_minute: raise AdmissionDenied('observation_rate_limit')
            self._buckets[key]=(start,count+1)
        if not self._slots.acquire(blocking=False): raise AdmissionDenied('observation_busy')
        try: yield
        finally: self._slots.release()
