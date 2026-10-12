"""Offline end-to-end T report smoke checks in the actual Linux build."""
import json,tempfile
from pathlib import Path
from ibkr_etl.monthly_artifacts import render_factsheet
from render_onboarding_historical_reports import _render_presentation

def main():
 root=Path('/app');template=root/'templates/U16088571/approved-august-2026-20-slide-v24.pptx'
 with tempfile.TemporaryDirectory(prefix='t-render-smoke-',dir='/tmp') as tmp:
  folder=Path(tmp)
  for period in ('2025-01-31','2026-09-30'):
   source=root/'t-runtime-tests/t-release-fixtures/inputs'/f'{period}.json'
   snapshot=json.loads(source.read_bytes())
   render_factsheet(snapshot,template_path=root/'templates/alpha-world-t/owner_template_20261008.pdf',output_path=folder/f'{period}.pdf')
   _render_presentation(template=template,snapshot=source,output=folder/f'{period}.pptx',period_end=period,
      subject_id='alpha-world-t',expected_strategy='iStrategy: Alpha World T')
 print('PASS: T earliest and latest PDF and PowerPoint rendered and financially validated without desktop environment',flush=True)

if __name__=='__main__':main()
