#!/usr/bin/env python3
"""Extract WebM frames and remove the opaque export matte."""
from __future__ import annotations
import argparse, hashlib, json, math
from pathlib import Path
import av
import numpy as np
from PIL import Image, ImageDraw

def sha256(path: Path) -> str:
    h=hashlib.sha256()
    with path.open('rb') as f:
        for b in iter(lambda:f.read(1024*1024), b''): h.update(b)
    return h.hexdigest()

def parse_size(s: str): return tuple(map(int,s.lower().split('x')))

def remove_matte(rgb, mode, threshold):
    a=rgb.astype(np.int16)
    dist=np.max(np.abs(a-(0 if mode=='near-black' else 255)),axis=2)
    alpha=np.where(dist<=threshold,0,255).astype(np.uint8)
    band=(dist>threshold)&(dist<=threshold+12); alpha[band]=np.clip((dist[band]-threshold)*20,0,255).astype(np.uint8)
    rgba=np.dstack([rgb,alpha]); rgba[alpha==0,:3]=0; return rgba

def fit_cell(im,size):
    w,h=size; im=im.convert('RGBA'); bbox=im.getbbox()
    if bbox: im=im.crop(bbox)
    scale=min((w-8)/im.width,(h-8)/im.height) if im.width and im.height else 1
    nw,nh=max(1,round(im.width*scale)),max(1,round(im.height*scale)); im=im.resize((nw,nh),Image.Resampling.LANCZOS)
    out=Image.new('RGBA',(w,h)); out.alpha_composite(im,((w-nw)//2,h-nh)); return out

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--input',required=True); ap.add_argument('--output',required=True); ap.add_argument('--key',choices=['near-black','near-white'],default='near-black'); ap.add_argument('--threshold',type=int,default=12); ap.add_argument('--cell',default='192x208'); ap.add_argument('--fps',type=float,default=30); args=ap.parse_args()
    src=Path(args.input); out=Path(args.output); fd=out/'frames'; fd.mkdir(parents=True,exist_ok=True); cell=parse_size(args.cell)
    c=av.open(str(src)); s=c.streams.video[0]; fps=float(s.average_rate) if s.average_rate else None; decoded=[]; times=[]
    for fr in c.decode(video=0):
        decoded.append(fr.to_ndarray(format='rgb24'))
        if fr.time is not None: times.append(float(fr.time))
    if fps is None and len(times)>1:
        diffs=np.diff(np.asarray(times)); diffs=diffs[diffs>0]
        if len(diffs): fps=float(1/np.median(diffs))
    if not decoded: raise RuntimeError('no video frames decoded')
    duration=(times[-1] if times else (len(decoded)-1)/(fps or args.fps))+1/(fps or args.fps)
    target_n=max(1,round(duration*args.fps)); indices=[]
    for k in range(target_n):
        t=k/args.fps
        if times:
            indices.append(min(range(len(times)), key=lambda i: abs(times[i]-t)))
        else:
            indices.append(min(len(decoded)-1, round(t*(fps or args.fps))))
    ratios=[]; n=0
    for idx in indices:
        im=fit_cell(Image.fromarray(remove_matte(decoded[idx],args.key,args.threshold),'RGBA'),cell); im.save(fd/f'frame-{n:04d}.png'); ratios.append(float(np.mean(np.asarray(im)[...,3]==0))); n+=1
    thumbs=[]
    for p in sorted(fd.glob('*.png')):
        im=Image.open(p).convert('RGBA'); im.thumbnail((96,104)); thumbs.append(im)
    cols=min(10,max(1,len(thumbs))); rows=math.ceil(len(thumbs)/cols); sheet=Image.new('RGBA',(cols*100,rows*108),(230,230,230,255)); d=ImageDraw.Draw(sheet)
    for i,im in enumerate(thumbs): sheet.alpha_composite(im,(i%cols*100+(100-im.width)//2,i//cols*108+2)); d.text((i%cols*100+2,i//cols*108+88),str(i),fill=(0,0,0,255))
    sheet.convert('RGB').save(out/'contact-sheet.png')
    report={'source':str(src.resolve()),'sha256':sha256(src),'input':{'width':s.width,'height':s.height,'fps':fps,'frames':n,'codec':s.codec_context.name,'pix_fmt':s.pix_fmt},'output':{'cell':list(cell),'frames':n,'fps':args.fps,'transparent_ratio_mean':sum(ratios)/len(ratios) if ratios else 1},'matte':{'key':args.key,'threshold':args.threshold,'alpha_band':12}}
    (out/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8'); print(json.dumps(report,ensure_ascii=False))
if __name__=='__main__': main()
