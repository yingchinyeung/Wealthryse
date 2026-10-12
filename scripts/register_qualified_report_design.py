"""Release operator only. Register a pipeline receipt, never bypass qualification."""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from ibkr_etl.db import connect
from ibkr_etl.report_design_updates import register_design
from ibkr_etl.util import canonical_json


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--subject',required=True)
    parser.add_argument('--version',required=True)
    parser.add_argument('--receipt',type=Path,required=True)
    parser.add_argument('--receipt-sha256',required=True)
    parser.add_argument('--actor',required=True)
    args=parser.parse_args()
    # The digest must be obtained independently from protected release evidence.
    import hashlib
    content=args.receipt.read_bytes()
    if hashlib.sha256(content).hexdigest()!=args.receipt_sha256:
        raise ValueError('Protected qualification receipt integrity failed')
    url=os.environ.get('RELEASE_DATABASE_URL','')
    if not url.startswith(('postgres://','postgresql://')):
        raise RuntimeError('A scoped release-service PostgreSQL connection is required')
    tenant=os.environ.get('PORTFOLIO_TENANT_ID','00000000-0000-0000-0000-000000000001')
    connection=connect(Path('/tmp/unused.sqlite3'),url,tenant)
    try:
        register_design(connection,tenant,args.subject,args.version,content,
            installed_image_sha256=os.environ.get('REPORT_RENDERER_IMAGE_SHA256',''),actor=args.actor)
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    print(canonical_json({'status':'registered','subject_id':args.subject,'design_version':args.version}))
    return 0


if __name__=='__main__':
    raise SystemExit(main())
