"""Read-only inspection of native PowerPoint export and source geometry."""
import json, zipfile
from pathlib import Path
from xml.etree import ElementTree as E
from pypdf import PdfReader

ROOT=Path(__file__).resolve().parents[1]
BASE=ROOT/'.work/alpha-t-internal-presentations-20261009-v1'
NS={'a':'http://schemas.openxmlformats.org/drawingml/2006/main','p':'http://schemas.openxmlformats.org/presentationml/2006/main'}
pages=PdfReader(BASE/'AlphaWorld_T_2026-09-30_NativeReview.pdf').pages
with zipfile.ZipFile(BASE/'output/AlphaWorld_T_Presentation_2026-09-30.pptx') as z:
 for number in range(1,21):
  root=E.fromstring(z.read(f'ppt/slides/slide{number}.xml'))
  shapes=[]
  for sp in root.findall('.//p:sp',NS):
   text=''.join(t.text or '' for t in sp.findall('.//a:t',NS))
   if text.startswith('iStrategy:') or text.startswith('Gross sleeve exposure:'):
    shapes.append({'text':text,'xml':E.tostring(sp,encoding='unicode')})
  page=pages[number-1]
  words=[]
  def visitor(text,cm,tm,font,size):
   if 'iStrategy:' in text or 'Gross sleeve' in text:words.append({'text':text,'cm':cm,'tm':tm,'font_size':size})
  page.extract_text(visitor_text=visitor)
  print(json.dumps({'page':number,'size':list(page.mediabox),'native_words':words,'shapes':shapes}))
