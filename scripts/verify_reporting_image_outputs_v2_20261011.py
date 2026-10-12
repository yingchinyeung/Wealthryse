"""Compare v4 installed output to the same native-reviewed reference files."""
from pathlib import Path
root=Path(__file__).resolve().parents[1]
source=(root/'scripts/verify_reporting_image_outputs_20261011.py').read_text()
for old,new,count in [('image-review-v1','image-review-v2',1),('image-qa-v1','image-qa-v2',2)]:
    if source.count(old)!=count:raise ValueError('Exact comparison anchors required')
    source=source.replace(old,new)
if __name__=='__main__':exec(compile(source,'verified-v4-output-comparison','exec'))
