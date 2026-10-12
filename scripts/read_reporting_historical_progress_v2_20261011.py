"""Compact progress for the separately retained v4 historical batch."""
from pathlib import Path
source=(Path(__file__).parent/'read_reporting_historical_progress_20261011.py').read_text()
if source.count("+'-v1')")!=1:raise ValueError('Exact progress path required')
source=source.replace("+'-v1')","+'-v2')")
if __name__=='__main__':exec(compile(source,'historical-v2-progress','exec'))
