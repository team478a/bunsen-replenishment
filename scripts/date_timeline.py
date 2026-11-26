"""Manual-only timeline of the reported forecast gaps; no inferred settings."""
from datetime import date
from reportlab.graphics.shapes import Drawing,Rect,Line,String
from reportlab.lib import colors

START=date(2026,11,19)
AVAILABLE_END=date(2026,12,2)
# The end dates below are read from the two error screenshots, not calculated
# from assumed warehouse lead times or replenishment periods.
CASES=[('画像1 神戸・7日',date(2026,12,4)),
       ('画像1 杉戸・7日',date(2026,12,5)),
       ('画像2 神戸・20日',date(2026,12,14)),
       ('画像2 杉戸・20日',date(2026,12,15))]

def shortage_days(end):return max(0,(end-AVAILABLE_END).days)

def shortage_timeline(font,bold,width=491):
    d=Drawing(width,218)
    left=107;right=width-9;span=27;unit=(right-left)/span
    navy=colors.HexColor('#183447');muted=colors.HexColor('#546879')
    blue=colors.HexColor('#2D7EAD');red=colors.HexColor('#C24D42')
    light=colors.HexColor('#EDF2F5');grid=colors.HexColor('#D9E1E6')
    def text(x,y,s,size=8.3,fill=navy,heavy=False):d.add(String(x,y,s,fontName=bold if heavy else font,fontSize=size,fillColor=fill))
    text(left,205,'必要な予測期間（青は取込済み、赤は不足）',9,heavy=True)
    for offset,label in [(0,'11/19'),(7,'11/26'),(14,'12/3'),(21,'12/10'),(26,'12/15')]:
        x=left+offset*unit
        text(x-2,186,label,8,muted)
        d.add(Line(x,30,x,179,strokeColor=grid,strokeWidth=.4))
    for index,(label,end) in enumerate(CASES):
        y=159-index*36
        text(0,y+1,label,8.5,heavy=True)
        d.add(Rect(left,y-1,right-left,11,fillColor=light,strokeColor=None))
        available=(AVAILABLE_END-START).days+1;missing=shortage_days(end)
        d.add(Rect(left,y-1,available*unit,11,fillColor=blue,strokeColor=None))
        d.add(Rect(left+available*unit,y-1,missing*unit,11,fillColor=red,strokeColor=None))
        text(left,y-14,f'必要末日 {end.month}/{end.day}   不足 12/3〜{end.month}/{end.day}（{missing}日分）',8.2)
    cutoff=left+14*unit
    d.add(Line(cutoff,34,cutoff,178,strokeColor=red,strokeWidth=.7,strokeDashArray=[2,2]))
    d.add(Rect(left,9,10,7,fillColor=blue,strokeColor=None));text(left+15,8,'取込済み 11/19〜12/2',8)
    d.add(Rect(left+175,9,10,7,fillColor=red,strokeColor=None));text(left+190,8,'未取込 12/3以降',8)
    return d

def requirement_stages(font,bold,width=491):
    d=Drawing(width,75);navy=colors.HexColor('#183447')
    d.add(Rect(0,22,302,38,fillColor=colors.HexColor('#E4F0F5'),strokeColor=None))
    d.add(Rect(307,22,width-307,38,fillColor=colors.HexColor('#FFF0D7'),strokeColor=None))
    def t(x,y,s,size=9,heavy=False):d.add(String(x,y,s,fontName=bold if heavy else font,fontSize=size,fillColor=navy))
    t(10,44,'在庫翌日〜今回の補充でまかなう最後の日',10,True)
    t(10,29,'到着までの日数＋補充対象期間で決まる',8.8)
    t(317,44,'その先の安全在庫日数',10,True)
    t(317,29,'通常7日 / 例外商品20日',8.8)
    t(2,7,'ここから予測が必要',8.5);t(width-153,7,'ここまで予測が必要',8.5)
    return d
