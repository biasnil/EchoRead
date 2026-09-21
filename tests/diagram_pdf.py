import pymupdf
def make(path):
    d=pymupdf.open(); p=d.new_page(width=595,height=842)
    y=60
    for t in ["1. Transmission Mode / Duplex Communication (FDD & TDD)","Simplex: transmit only one way on a channel","Full-duplex: two-way communication, achieved using FDD or TDD"]:
        p.insert_text((40,y),t,fontsize=12); y+=32
    p.insert_text((40,y+20),"FDD (Frequency Division Duplex):",fontsize=12)
    # diagram: pill, two arrows, phone, tower, two boxes with labels
    p.draw_rect(pymupdf.Rect(230,200,320,225), color=(0.3,0.5,0.3), radius=0.4); p.insert_text((258,218),"FDD",fontsize=13)
    p.draw_polyline([(150,260),(400,260),(400,250),(430,268),(400,286),(400,276),(150,276)], color=None, fill=(0.6,0.8,0.3), closePath=True)
    p.insert_text((250,250),"Download",fontsize=10)
    p.draw_polyline([(430,320),(180,320),(180,310),(150,328),(180,346),(180,336),(430,336)], color=None, fill=(0.2,0.6,0.2), closePath=True)
    p.insert_text((260,365),"Upload",fontsize=10)
    p.draw_rect(pymupdf.Rect(450,250,490,340), color=None, fill=(0.05,0.1,0.4))
    p.draw_line((80,250),(100,340),color=(0.3,0.3,0.6),width=2); p.draw_line((120,250),(100,340),color=(0.3,0.3,0.6),width=2); p.draw_line((85,300),(115,300),color=(0.3,0.3,0.6),width=2)
    p.draw_rect(pymupdf.Rect(60,420,170,500),color=(0.2,0.3,0.5)); p.insert_text((75,450),"Mobile",fontsize=11); p.insert_text((75,466),"Terminal",fontsize=11); p.insert_text((100,482),"M",fontsize=11)
    p.draw_rect(pymupdf.Rect(430,420,540,500),color=(0.2,0.3,0.5)); p.insert_text((450,450),"Base Station",fontsize=11); p.insert_text((480,472),"B",fontsize=11)
    p.draw_polyline([(200,445),(400,445),(400,455),(200,455)],color=(0.4,0.4,0.4),fill=(0.85,0.85,0.85),closePath=True); p.insert_text((250,452),"Forward Channel",fontsize=10)
    p.draw_polyline([(200,458),(400,458),(400,470),(200,470)],color=(0.4,0.4,0.4),fill=(0.85,0.85,0.85),closePath=True); p.insert_text((250,467),"Reverse Channel",fontsize=10)
    p.insert_text((40,560),"Forward channel and reverse channel use different frequencies",fontsize=12)
    d.save(path)
if __name__=="__main__": make("/tmp/diagram.pdf")
