"""Offline source-bound replay for the operating image, no DB or provider I/O."""
import json
from pathlib import Path
from ibkr_etl.alpha_world_t_presentation import build_model
from ibkr_etl.alpha_world_t_runtime import build_payload,validate_payload
from ibkr_etl.subject_onboarding import approved_reference_definitions,configuration_sha256

def main():
 paths=sorted((Path(__file__).parent/'inputs').glob('*.json'))
 assert len(paths)==21
 assert configuration_sha256(approved_reference_definitions()['alpha-world-t'])=='99b9d84e8118632b59dfd73fa64c08c05af409bb05a3daa34dd2fe78173f0209'
 for path in paths:
  s=json.loads(path.read_bytes());m=build_model(s)
  inputs={'subject_id':'alpha-world-t','subject_type':'composite_portfolio','as_of':s['period_end'],
   'benchmark_id':None,'rows':[],'positions':[],'risk_free':s['risk']['risk_free'],
   'crisis_replay':s['risk']['crisis_replay'],'t_hkd_snapshot':s}
  p=build_payload(inputs);validate_payload(p)
  for key,value in p['metrics'].items():
   if key in m['risk']:assert value==m['risk'][key],(path.stem,key)
 print(json.dumps({'status':'passed','months_verified':len(paths),'database_connected':False}))

if __name__=='__main__':main()
