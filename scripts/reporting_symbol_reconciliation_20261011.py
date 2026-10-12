"""Qualification-only alias reconciliation. Never modify broker/report records.

The broker's August 27/28 2026 OpenPositions explicitly identify contract
804144296 as PSKY. The same contract is SKYD in the retained instrument master.
This validates a display-label difference, NOT predecessor/price continuity.
"""
from copy import deepcopy

CONID='804144296'
BROKER_HASHES={'2026-08-27':'d64b402a167f7493a9772a4850f6743f4c56c9ce1f2d532b067808fa4a7002e8',
              '2026-08-28':'2f0c0b1dd8c318272a10f3d43b6a22c7856eee6aa3a0414c5c8eaf1572cb1b27'}
MASTER_SHA='530f9338da38c05faa907cb24ad260836ce1c4e0972e663f69c1b57c9d7f2e37'
SOURCE='https://www.nasdaqtrader.com/TraderNews.aspx?id=ECA2025-414'


def validate_evidence(instrument, broker):
    if (instrument.get('conid'),instrument.get('symbol'),instrument.get('asset_category'),instrument.get('currency'),instrument.get('source_sha256'))!=(CONID,'SKYD','STK','USD',MASTER_SHA):
        raise ValueError('Instrument identity provenance differs')
    found={r['report_date']:r for r in broker}
    if len(found)!=2 or len(broker)!=2 or set(found)!=set(BROKER_HASHES):
        raise ValueError('Both dated broker records required')
    for day,digest in BROKER_HASHES.items():
        row=found[day]
        if (str(row.get('conid')),row.get('symbol'),row.get('description'),row.get('section'),row.get('record_type'),row.get('row_sha256'))!=(CONID,'PSKY','PARAMOUNT SKYDANCE CL B','OpenPositions','OpenPosition',digest):
            raise ValueError('Broker identity evidence differs')


def compare(actual, expected, *, subject, as_of):
    def indexed(rows):
        result={str(r['conid']):r for r in rows}
        if len(result)!=len(rows):raise ValueError('Duplicate classification identity')
        return result
    left=deepcopy(indexed(actual));right=indexed(expected);reconciled=[]
    if set(left)!=set(right):raise ValueError('Classification identities differ')
    for key,row in left.items():
        if row==right[key]:continue
        if (subject,as_of,key,row.get('symbol'),right[key].get('symbol'))!=('U16088571','2026-09-30',CONID,'SKYD','PSKY'):
            raise ValueError('Unqualified classification difference')
        row['symbol']='PSKY'
        if row!=right[key]:raise ValueError('Non-symbol classification fields differ')
        reconciled.append({'conid':key,'broker_master_symbol':'SKYD','frozen_report_symbol':'PSKY',
            'basis':'two hash-verified dated broker position records and Nasdaq issuer notice',
            'predecessor_price_continuity_asserted':False})
    return reconciled
