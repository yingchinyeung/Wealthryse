import json,zipfile
from pathlib import Path
from xml.etree import ElementTree as E
root=Path(__file__).resolve().parents[1]
path=root/'.work/alpha-t-internal-presentations-20261009-v1/output/AlphaWorld_T_Presentation_2026-09-30.pptx'
a='{http://schemas.openxmlformats.org/drawingml/2006/main}'
with zipfile.ZipFile(path) as z:
 slide=E.fromstring(z.read('ppt/slides/slide3.xml'))
 table=slide.find('.//'+a+'tbl')
 for row in table.findall(a+'tr'):
  for cell in row.findall(a+'tc'):
   print(E.tostring(cell,encoding='unicode'))
