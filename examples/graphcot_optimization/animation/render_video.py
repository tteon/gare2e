"""Render the educational subgraphs to an MP4; no database/model is contacted."""
from __future__ import annotations
import argparse
import hashlib
import json
import math
from pathlib import Path
import subprocess
from PIL import Image, ImageDraw, ImageFont

ROOT=Path(__file__).resolve().parent
BG='#f5f7fb';FG='#142b42';MUTED='#556a7f';FAINT='#dbe3ec';BEFORE='#b56d27';AFTER='#216bc0';GREEN='#20815d';RED='#b34747'


def main() -> None:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('--font',required=True,type=Path,help='Korean-capable font file, e.g. NotoSansCJK-Regular.ttc')
    parser.add_argument('--fps',type=int,default=24)
    args=parser.parse_args()
    if args.output.exists():parser.error('refusing overwrite')
    if args.fps<1 or args.fps>60:parser.error('fps must be1..60')
    args.output.parent.mkdir(parents=True,exist_ok=True)
    data_bytes=(ROOT/'subgraph-data.json').read_bytes()
    data=json.loads(data_bytes)
    fonts={n:ImageFont.truetype(str(args.font),n) for n in [22,25,28,32,38,46]}

    def frame(ci: int, seconds: float) -> Image.Image:
        c=data['cases'][ci];step=min(6,int(seconds/3));progress=min(.999,(seconds%3)/3)
        im=Image.new('RGB',(1920,1080),BG);draw=ImageDraw.Draw(im)

        def text(x: float,y: float,s: str,size: int=28,color: str=FG,center: bool=False) -> None:
            if center:
                box=draw.textbbox((0,0),s,font=fonts[size]);x-=(box[2]-box[0])/2
            draw.text((int(x),int(y)),s,font=fonts[size],fill=color)

        text(70,42,c['id']+'  '+c['title'],46)
        text(70,104,'노드 → 관계 → 노드: 후보 서브그래프가 펼쳐지고 줄어드는 방식',28,MUTED)
        text(1820,55,f'{ci+1} / 3',28,MUTED,True)
        for side,ox,color in [('before',65,BEFORE),('after',1025,AFTER)]:
            phase=c[side][step]
            text(ox,171,'기존 · 넓은 후보 탐색' if side=='before' else '최적화 · 후보를 먼저 축소',32,color)
            text(ox,222,f'{step+1}/7  '+phase['label'],28)
            nodes={n['id']:{**n,'x':ox+30+n['x'],'y':300+n['y']} for n in c['nodes']}
            active=phase['edges'][:min(len(phase['edges']),int(progress*len(phase['edges']))+1)]
            focus=active[-1] if active else None
            lit_nodes={v for e in c['edges'] if e['id'] in active for v in [e['a'],e['b']]}
            # Draw structure before the selected edges, so focused paths stay visible.
            for e in sorted(c['edges'],key=lambda e:e['id'] in active):
                a,b=nodes[e['a']],nodes[e['b']];dx,dy=b['x']-a['x'],b['y']-a['y'];length=math.hypot(dx,dy)
                x1,y1=a['x']+dx*25/length,a['y']+dy*25/length
                x2,y2=b['x']-dx*25/length,b['y']-dy*25/length
                bend=30 if e['id']=='rev' else 30 if e['id']=='t1' and c['id']=='L6' else 0
                cx,cy=(x1+x2)/2-dy/length*bend,(y1+y2)/2+dx/length*bend
                points=[]
                for j in range(33):
                    t=j/32;q=1-t;points.append((q*q*x1+2*q*t*cx+t*t*x2,q*q*y1+2*q*t*cy+t*t*y2))
                lit=e['id'] in active;col=color if lit else FAINT
                draw.line(points,fill=col,width=4 if lit else 2)
                angle=math.atan2(points[-1][1]-points[-2][1],points[-1][0]-points[-2][0]);size=12 if lit else 8
                draw.polygon([(x2,y2),(x2-size*math.cos(angle-.4),y2-size*math.sin(angle-.4)),(x2-size*math.cos(angle+.4),y2-size*math.sin(angle+.4))],fill=col)
                if lit:
                    label=e['label'];lx,ly=(cx+48,cy-13) if abs(dx)<20 else (cx,cy-32);bbox=draw.textbbox((0,0),label,font=fonts[22]);w=bbox[2]-bbox[0]
                    draw.rectangle((lx-w/2-5,ly+2,lx+w/2+5,ly+28),fill=BG)
                    text(lx,ly,label,22,FG,True)
                if e['id']==focus:
                    t=(progress*len(phase['edges']))%1;q=1-t;x=q*q*x1+2*q*t*cx+t*t*x2;y=q*q*y1+2*q*t*cy+t*t*y2
                    draw.ellipse((x-7,y-7,x+7,y+7),fill=color)
            for n in nodes.values():
                x,y=n['x'],n['y'];selected=n['id'] in phase['selected'];rejected=n['id'] in phase['rejected'];lit=n['id'] in lit_nodes
                fill='#e0f2e8' if selected else '#e4edf9' if lit else BG
                stroke=RED if rejected else GREEN if selected else color if lit else '#9aabbd'
                if n['type']=='Account':draw.ellipse((x-25,y-25,x+25,y+25),fill=fill,outline=stroke,width=3)
                elif n['type']=='Medium':draw.polygon([(x,y-27),(x+34,y),(x,y+27),(x-34,y)],fill=fill,outline=stroke,width=3)
                else:draw.rounded_rectangle((x-30,y-23,x+30,y+23),radius=0 if n['type']=='Loan' else 8,fill=fill,outline=stroke,width=3)
                text(x,y-16,n['id'],25,FG,True);text(x,y+30,n['type'],22,MUTED,True)
                if rejected:
                    draw.line((x-17,y-17,x+17,y+17),fill=RED,width=4);draw.line((x+17,y-17,x-17,y+17),fill=RED,width=4)
            text(ox,842,'별도 SF1 실측: DB hits',25,MUTED)
            text(ox,878,f"{c['hits'][0 if side=='before' else 1]:,}",38)
        draw.line((960,170,960,935),fill=FAINT,width=2)
        text(70,956,c['scope'],25,MUTED)
        text(70,1000,'설명용 그래프 · 개수/속도 비례 아님 · DB hits는 논리 접근, 물리 disk read 아님',25,MUTED)
        # Chapter playback progress; not a performance or DB progress meter.
        draw.rectangle((70,1052,1850,1058),fill=FAINT)
        draw.rectangle((70,1052,70+1780*(ci*21+seconds)/63,1058),fill=AFTER)
        return im

    command=['ffmpeg','-hide_banner','-loglevel','error','-n','-f','rawvideo','-pix_fmt','rgb24','-s','1920x1080','-r',str(args.fps),'-i','pipe:0','-an','-c:v','libx264','-preset','fast','-crf','21','-pix_fmt','yuv420p','-movflags','+faststart',str(args.output)]
    with subprocess.Popen(command,stdin=subprocess.PIPE) as encoder:
        try:
            for ci in range(3):
                for f in range(21*args.fps):
                    im=frame(ci,f/args.fps)
                    if f in [int(7.5*args.fps),int(18.5*args.fps)]:im.save(args.output.parent/f'{args.output.stem}-{ci+1}-{f}.png')
                    encoder.stdin.write(im.tobytes())
                print(f'Rendered {data["cases"][ci]["id"]}',flush=True)
        finally:
            encoder.stdin.close()
        if encoder.wait()!=0:raise RuntimeError('ffmpeg failed')
    receipt=dict(file=args.output.name,sha256=hashlib.sha256(args.output.read_bytes()).hexdigest(),bytes=args.output.stat().st_size,width=1920,height=1080,fps=args.fps,duration_seconds=63,audio=False,kind='educational synthetic graph, not profiler replay',data_sha256=hashlib.sha256(data_bytes).hexdigest(),font_sha256=hashlib.sha256(args.font.read_bytes()).hexdigest())
    args.output.with_suffix('.json').write_text(json.dumps(receipt,indent=2)+'\n')
    print(json.dumps(receipt),flush=True)


if __name__=='__main__':
    main()
