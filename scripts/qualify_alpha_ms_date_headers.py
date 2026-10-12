"""Render all MS months; prove only the two date headers changed from v14r14."""
import math
from PIL import Image,ImageDraw

import qualify_alpha_ms_subtitle as qa
from ibkr_etl.alpha_world_ms_pdf_validation import validate_date_headers

qa.OUTPUT=qa.ROOT/'output/pdf/AlphaWorld_MS_Date_Headers_v14r15'
qa.QA=qa.ROOT/'tmp/pdfs/alpha-ms-date-headers-v14r15'
qa.BASE=qa.ROOT/'output/pdf/AlphaWorld_MS_Subtitle_v14r14'


def main():
    qa.main(changed_regions={1:(480,781,590,819),2:(480,798,590,838)},
            extra_validation=validate_date_headers)
    crops=[]
    for folder in sorted(qa.QA.iterdir()):
        if folder.is_dir():
            image=Image.open(folder/'after-2.png').convert('RGB')
            crops.append((folder.name,image.crop((int(image.width*.77),0,image.width,90))))
    w,h=crops[0][1].width,112
    contact=Image.new('RGB',(w*3,h*math.ceil(len(crops)/3)),'white');draw=ImageDraw.Draw(contact)
    for i,(month,image) in enumerate(crops):
        x,y=i%3*w,i//3*h;draw.text((x+5,y+3),month,fill='black');contact.paste(image,(x,y+22))
    contact.save(qa.QA/'all-21-page-two-dates.png')
    print(qa.archive())


if __name__=='__main__':main()
