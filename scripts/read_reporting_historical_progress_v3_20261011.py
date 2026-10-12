"""Read progress only for the corrected v3 historical batches."""
from pathlib import Path
source = (Path(__file__).parent / 'read_reporting_historical_progress_20261011.py').read_text()
if source.count("+'-v1')") != 1:
    raise ValueError('Exact progress path required')
source = source.replace("+'-v1')", "+'-v3')")
if __name__ == '__main__':
    exec(compile(source, 'historical-v3-progress', 'exec'))
