"""Read-only slide preview assembly for visual inspection, not report authoring."""
from pathlib import Path
import sys
from PIL import Image,ImageDraw
source=Path(sys.argv[1]);destination=Path(sys.argv[2]);destination.mkdir(exist_ok=False)
files=sorted(source.glob('slide-*.png'))
if len(files)!=20:raise ValueError('Expected exactly 20 rendered slides')
for start in range(0,20,4):
    canvas=Image.new('RGB',(1920,1140),'#dddddd');draw=ImageDraw.Draw(canvas)
    for i,p in enumerate(files[start:start+4]):
        with Image.open(p) as raw:
            view=raw.convert('RGB');view.thumbnail((948,534))
            x=(i%2)*960+6;y=(i//2)*570+28
            canvas.paste(view,(x,y));draw.text((x,y-20),p.stem,fill='black')
    canvas.save(destination/f'contact-{start//4+1}.png')
print(destination)
