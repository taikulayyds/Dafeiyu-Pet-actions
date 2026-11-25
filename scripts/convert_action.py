#!/usr/bin/env python3
"""Deterministic VP9/WebM -> 12 fps static WebP action packages.

Requires Python 3.10+, ffmpeg/ffprobe (libvpx-vp9), and cwebp. No third-party
Python modules and no upstream code execution. See README.md for the package contract.
"""
from __future__ import annotations
import argparse, hashlib, json, os, re, shutil, struct, subprocess, sys, tempfile, unicodedata, zipfile, zlib
from fractions import Fraction
from pathlib import Path, PurePosixPath
from typing import Any

REPOSITORY = "https://github.com/PC2005-cloud/dsh-pet"
LICENSE_ID = "CC-BY-NC-SA-4.0"
FPS = 12
QUALITY = 85
MAX_PACKAGE = 8 * 1024 * 1024
PROFILE = {"name":"static-webp-v1", "fps":12, "sampling":"ffmpeg fps=12:start_time=0:round=near, tail clone padding and explicit frame cap; ceil(duration*12), snap <=0.5ms container rounding to nearest tick; final duration closes reported source milliseconds", "webp":"cwebp -q 85 -alpha_q 100 -m 4 -metadata none -exact", "canvas":"640x360 source canvas, no scale/crop", "archive":"ZIP_DEFLATED level 9, fixed timestamps and permissions"}
ID_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
HEX40 = re.compile(r"^[0-9a-fA-F]{40}$")

class ConversionError(RuntimeError): pass

def sha256_file(path: Path) -> str:
    h=hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''): h.update(block)
    return h.hexdigest()

def clean_environment() -> dict[str,str]:
    """Media tools never inherit provider tokens, proxy credentials or loader hooks."""
    keep=('PATH','HOME','LANG','LC_ALL','TMPDIR','SYSTEMROOT')
    return {key:os.environ[key] for key in keep if key in os.environ}

def run(args: list[str], timeout: int=1800) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              check=True, timeout=timeout, shell=False, env=clean_environment())
    except FileNotFoundError as e: raise ConversionError(f"required executable not found: {args[0]}") from e
    except subprocess.TimeoutExpired as e: raise ConversionError(f"timed out: {args[0]}") from e
    except subprocess.CalledProcessError as e:
        detail=(e.stderr or b'').decode('utf-8','replace')[-3000:]
        raise ConversionError(f"command failed ({e.returncode}): {args[0]}: {detail}") from e

def tool_version(cmd: str) -> str:
    p=run([cmd, '-version'] if cmd=='ffmpeg' else [cmd, '-version'], timeout=30)
    return (p.stdout or p.stderr).decode('utf-8','replace').splitlines()[0][:300]

def stable_clip_id(title: str, source_file: str, mapping: dict[str,str] | None=None) -> str:
    """Resolve explicit source-path mapping first; otherwise stable ASCII slug+hash."""
    if mapping and source_file in mapping:
        clip=mapping[source_file]
        if not isinstance(clip,str) or not ID_RE.fullmatch(clip): raise ConversionError(f"invalid clip id mapped for {source_file!r}")
        return clip
    if title == '悠闲哼歌' or PurePosixPath(source_file).stem == '悠闲哼歌': return 'youxian-hengga'
    ascii_title=unicodedata.normalize('NFKD',title).encode('ascii','ignore').decode().lower()
    slug='-'.join(re.findall(r'[a-z0-9]+',ascii_title))[:48].strip('-') or 'action'
    suffix=hashlib.sha256(source_file.encode('utf-8')).hexdigest()[:10]
    clip=f'{slug}-{suffix}'
    if not ID_RE.fullmatch(clip): raise ConversionError('could not derive a safe clip id')
    return clip

def _fraction(value: str) -> Fraction:
    try: return Fraction(value)
    except Exception as e: raise ConversionError(f"invalid rational number from ffprobe: {value!r}") from e

def probe(source: Path, ffprobe: str) -> dict[str,Any]:
    p=run([ffprobe,'-protocol_whitelist','file,pipe','-v','error','-select_streams','v:0','-count_frames','-show_streams','-show_format','-of','json',str(source)], timeout=600)
    try: data=json.loads(p.stdout)
    except Exception as e: raise ConversionError('ffprobe returned invalid JSON') from e
    streams=data.get('streams') or []
    if not streams: raise ConversionError('source has no video stream')
    s=streams[0]
    try: w=int(s['width']); h=int(s['height'])
    except Exception as e: raise ConversionError('source dimensions are missing') from e
    if (w,h)!=(640,360): raise ConversionError(f"source canvas requires review: {w}x{h}; expected 640x360")
    duration=None
    if s.get('duration_ts') not in (None,'N/A') and s.get('time_base'):
        duration=Fraction(int(s['duration_ts']))*_fraction(s['time_base'])
    if not duration or duration<=0:
        raw=s.get('duration') or (data.get('format') or {}).get('duration')
        if raw and raw!='N/A': duration=_fraction(raw)
    if not duration or duration<=0 or duration>60: raise ConversionError('source duration must be positive and <=60 seconds')
    count=s.get('nb_read_frames') or s.get('nb_frames')
    try: count=int(count) if count not in (None,'N/A') else None
    except ValueError: count=None
    rate=s.get('avg_frame_rate') or s.get('r_frame_rate') or '0/0'
    try: source_fps=_fraction(rate)
    except ConversionError: source_fps=Fraction(0)
    if s.get('codec_name')!='vp9': raise ConversionError('source must use VP9 video')
    tags={str(k).lower():str(v) for k,v in (s.get('tags') or {}).items()}
    return {"width":w,"height":h,"duration":duration,"duration_ms":round(duration*1000),"source_frames":count,
            "source_fps":source_fps,"codec":s.get('codec_name'),"pixel_format":s.get('pix_fmt'),
            "alpha_mode":tags.get('alpha_mode',s.get('alpha_mode'))}

# PNG decoder for ffmpeg's forced RGBA output; checks alpha without Pillow.
def png_rgba_alpha(path: Path) -> tuple[int,int,int,int]:
    data=path.read_bytes()
    if not data.startswith(b'\x89PNG\r\n\x1a\n'): raise ConversionError(f"invalid PNG frame: {path.name}")
    pos=8; w=h=ct=bd=None; chunks=[]
    while pos+12<=len(data):
        n=struct.unpack('>I',data[pos:pos+4])[0]; typ=data[pos+4:pos+8]; end=pos+12+n
        if end>len(data): raise ConversionError(f"truncated PNG: {path.name}")
        chunk=data[pos+8:pos+8+n]
        if typ==b'IHDR': w,h,bd,ct,_,_,_=struct.unpack('>IIBBBBB',chunk)
        elif typ==b'IDAT': chunks.append(chunk)
        elif typ==b'IEND': break
        pos=end
    if not w or not h or bd!=8 or ct!=6: raise ConversionError(f"expected 8-bit RGBA PNG, got color type {ct}, bit depth {bd}")
    stride=w*4
    try: raw=zlib.decompress(b''.join(chunks))
    except zlib.error as e: raise ConversionError(f"bad PNG compressed data: {path.name}") from e
    if len(raw)!=h*(stride+1): raise ConversionError(f"unexpected PNG raster length: {path.name}")
    # ffmpeg is instructed to use PNG predictor ``none``; scan alpha bytes in C.
    # Retain a standards-correct slow fallback for unexpected filters.
    if all(raw[y*(stride+1)]==0 for y in range(h)):
        alpha=b''.join(raw[y*(stride+1)+4:y*(stride+1)+1+stride:4] for y in range(h))
        return w,h,min(alpha,default=255),max(alpha,default=0)
    prev=bytearray(stride); amin=255; amax=0; off=0
    for _ in range(h):
        ft=raw[off]; off+=1; scan=bytearray(raw[off:off+stride]); off+=stride
        if ft==1:
            for i in range(stride): scan[i]=(scan[i]+(scan[i-4] if i>=4 else 0))&255
        elif ft==2:
            for i in range(stride): scan[i]=(scan[i]+prev[i])&255
        elif ft==3:
            for i in range(stride): scan[i]=(scan[i]+((scan[i-4] if i>=4 else 0)+prev[i])//2)&255
        elif ft==4:
            for i in range(stride):
                a=scan[i-4] if i>=4 else 0; b=prev[i]; c=prev[i-4] if i>=4 else 0
                p=a+b-c; pa=abs(p-a); pb=abs(p-b); pc=abs(p-c)
                q=a if pa<=pb and pa<=pc else b if pb<=pc else c
                scan[i]=(scan[i]+q)&255
        elif ft!=0: raise ConversionError(f"unsupported PNG filter {ft}")
        vals=scan[3::4]; amin=min(amin,min(vals)); amax=max(amax,max(vals)); prev=scan
    return w,h,amin,amax

def _ceil_fraction(v: Fraction) -> int: return (v.numerator+v.denominator-1)//v.denominator

def sampling_count(duration: Fraction) -> int:
    """Container timecodes round to milliseconds: suppress <=0.5ms fake tail.

    4.917s is the millisecond representation of 59/12s, not a 60th frame.
    Preserve the reported 4917ms total; only the frame count is snapped.
    """
    ticks=duration*FPS
    nearest=round(ticks)
    if nearest>0 and abs(duration-Fraction(nearest,FPS))<=Fraction(1,2000):
        return nearest
    return _ceil_fraction(ticks)

def frame_durations(count: int,total_ms: int) -> list[int]:
    if count<1 or total_ms<1: raise ConversionError('duration rounds to less than one millisecond')
    out=[]
    for i in range(count-1): out.append(round(Fraction(i+1,12)*1000)-round(Fraction(i,12)*1000))
    out.append(total_ms-sum(out))
    if any(x<1 for x in out): raise ConversionError(f"cannot represent {count} frame durations in {total_ms} ms")
    return out

def _webp_info(path: Path) -> tuple[int|None,int|None,bool]:
    d=path.read_bytes()
    if len(d)<12 or d[:4]!=b'RIFF' or d[8:12]!=b'WEBP' or struct.unpack('<I',d[4:8])[0]+8!=len(d):
        raise ConversionError(f"invalid WebP RIFF: {path.name}")
    pos=12; width=height=None; alpha=False; bitstream=False
    while pos+8<=len(d):
        kind=d[pos:pos+4]; n=struct.unpack('<I',d[pos+4:pos+8])[0]; start=pos+8; end=start+n
        if end>len(d): raise ConversionError(f"truncated WebP chunk: {path.name}")
        if kind in (b'ANIM',b'ANMF'): raise ConversionError(f"animated WebP is forbidden: {path.name}")
        if kind==b'VP8X' and n>=10:
            flags=d[start]; alpha=bool(flags&0x10)
            width=1+int.from_bytes(d[start+4:start+7],'little'); height=1+int.from_bytes(d[start+7:start+10],'little')
        elif kind==b'ALPH': alpha=True
        elif kind==b'VP8 ':
            if n<10 or d[start+3:start+6]!=b'\x9d\x01\x2a': raise ConversionError('bad VP8 frame header')
            bitstream=True
            vw=int.from_bytes(d[start+6:start+8],'little')&0x3fff
            vh=int.from_bytes(d[start+8:start+10],'little')&0x3fff
            if width is not None and (width,height)!=(vw,vh): raise ConversionError('WebP canvas/bitstream mismatch')
            width,height=vw,vh
        elif kind==b'VP8L':
            if n<5 or d[start]!=0x2f: raise ConversionError('bad VP8L frame header')
            bits=int.from_bytes(d[start+1:start+5],'little'); vw=(bits&0x3fff)+1; vh=((bits>>14)&0x3fff)+1
            if width is not None and (width,height)!=(vw,vh): raise ConversionError('WebP canvas/bitstream mismatch')
            width,height=vw,vh; alpha=alpha or bool(bits&(1<<28)); bitstream=True
        pos=end+(n&1)
    if pos!=len(d) or not bitstream: raise ConversionError(f"bad WebP chunks or missing bitstream: {path.name}")
    return width,height,alpha

def verify_action(action_dir: Path, zip_path: Path|None=None) -> dict[str,Any]:
    if action_dir.is_symlink() or not action_dir.is_dir(): raise ConversionError('unsafe action directory')
    for name in ('manifest.json','LICENSE.txt'):
        p=action_dir/name
        if p.is_symlink() or not p.is_file(): raise ConversionError(f'unsafe or missing {name}')
    if zip_path is not None and (zip_path.is_symlink() or not zip_path.is_file()): raise ConversionError('unsafe or missing ZIP')
    try: m=json.loads((action_dir/'manifest.json').read_text('utf-8'))
    except Exception as e: raise ConversionError(f"cannot read manifest in {action_dir}") from e
    frames=m.get('frames')
    if not isinstance(frames,list) or not frames or type(m.get('frameCount')) is not int or m['frameCount']!=len(frames) or len(frames)>720: raise ConversionError('manifest frameCount mismatch')
    total=m.get('durationMs')
    if type(total) is not int or not 1<=total<=60000 or total!=sum(f.get('durationMs',0) for f in frames):
        raise ConversionError('manifest duration total mismatch')
    if [f.get('durationMs') for f in frames]!=frame_durations(len(frames),total): raise ConversionError('manifest is not on the 12fps timing grid')
    terminal=round(Fraction(len(frames)*1000,FPS))-round(Fraction((len(frames)-1)*1000,FPS))
    if frames[-1]['durationMs']>terminal+1 or sampling_count(Fraction(total,1000))!=len(frames):
        raise ConversionError('terminal frame/count exceeds the 12fps container rounding grid')
    if (m.get('width'),m.get('height'))!=(640,360): raise ConversionError('manifest canvas mismatch')
    expected={'manifest.json','LICENSE.txt'}
    for i,f in enumerate(frames):
        if set(f)!={'file','bytes','sha256','durationMs'} or type(f['durationMs']) is not int or type(f['bytes']) is not int:
            raise ConversionError('invalid frame schema/types')
        rel=f.get('file')
        if rel!=f"frames/{i:03d}.webp": raise ConversionError(f"unsafe or noncanonical frame path: {rel!r}")
        expected.add(rel); p=action_dir/rel
        if not p.is_file() or p.is_symlink(): raise ConversionError(f"missing or unsafe frame: {rel}")
        b=p.stat().st_size
        if not 0<b<=256*1024 or b!=f.get('bytes') or sha256_file(p)!=f.get('sha256'): raise ConversionError(f"frame hash/length mismatch or above 256 KiB: {rel}")
        w,h,alpha=_webp_info(p)
        if w is not None and (w!=m['width'] or h!=m['height']): raise ConversionError(f"WebP canvas mismatch: {rel}")
        if w is not None and not alpha: raise ConversionError(f"WebP lost alpha: {rel}")
    actual=set()
    for root,dirs,files in os.walk(action_dir,followlinks=False):
        if any((Path(root)/d).is_symlink() for d in dirs): raise ConversionError('symlink in action package')
        actual.update((Path(root)/f).relative_to(action_dir).as_posix() for f in files)
    if actual!=expected: raise ConversionError(f"unexpected/missing action files: {sorted(actual^expected)}")
    if not (action_dir/'LICENSE.txt').read_bytes(): raise ConversionError('empty LICENSE.txt')
    if zip_path and zip_path.exists():
        if zip_path.is_symlink() or zip_path.stat().st_size>MAX_PACKAGE: raise ConversionError('unsafe or oversized ZIP')
        with zipfile.ZipFile(zip_path) as z:
            names=z.namelist()
            if len(names)!=len(expected) or set(names)!=expected or any(n.endswith('/') or '..' in PurePosixPath(n).parts for n in names): raise ConversionError('ZIP member list is unsafe or differs from package')
            for n in names:
                if z.read(n)!=(action_dir/n).read_bytes(): raise ConversionError(f"ZIP content differs: {n}")
    return m

def _remove_owned_tree(path: Path, *, ignore_errors: bool=False) -> None:
    """Remove only our private stage/newly installed tree, never follow links.

    Path-based traversal also works on iSH mounts where stat and fstat expose
    different device IDs and shutil's fd check misidentifies real directories.
    Concurrent builds into the same output directory are unsupported.
    """
    try:
        if path.is_symlink(): raise ConversionError('refusing symlink tree removal')
        for base,dirs,files in os.walk(path,topdown=False,followlinks=False):
            for name in files: (Path(base)/name).unlink()
            for name in dirs:
                p=Path(base)/name
                if p.is_symlink(): p.unlink()
                else: p.rmdir()
        path.rmdir()
    except OSError:
        if not ignore_errors: raise

def _atomic_json(path: Path,data: Any) -> None:
    path.parent.mkdir(parents=True,exist_ok=True)
    fd,tmp=tempfile.mkstemp(prefix='.tmp-',dir=path.parent)
    try:
        with os.fdopen(fd,'w',encoding='utf-8',newline='\n') as f: json.dump(data,f,ensure_ascii=False,indent=2,sort_keys=True); f.write('\n'); f.flush(); os.fsync(f.fileno())
        os.replace(tmp,path)
    finally:
        if os.path.exists(tmp): os.unlink(tmp)

def _zip_create(action_dir: Path, target: Path, members: list[str]) -> None:
    if len(set(members))!=len(members): raise ConversionError('duplicate ZIP member')
    for name in members:
        rel=PurePosixPath(name)
        if rel.is_absolute() or not name or '\\' in name or any(x in ('','..','.') for x in name.split('/')):
            raise ConversionError(f'unsafe ZIP member: {name!r}')
        if (action_dir/name).is_symlink(): raise ConversionError('symlink ZIP member')
    target.parent.mkdir(parents=True,exist_ok=True)
    tmp=target.with_name(target.name+'.tmp')
    try:
        with zipfile.ZipFile(tmp,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=9,strict_timestamps=True) as z:
            for name in members:
                info=zipfile.ZipInfo(name,(1980,1,1,0,0,0)); info.compress_type=zipfile.ZIP_DEFLATED; info.create_system=3; info.external_attr=(0o100644<<16); info.flag_bits|=0x800
                z.writestr(info,(action_dir/name).read_bytes(),compress_type=zipfile.ZIP_DEFLATED,compresslevel=9)
        os.replace(tmp,target)
    finally:
        if tmp.exists(): tmp.unlink()

def convert_action(source: Path, out_dir: Path, clip: str, title: str, source_commit: str,
                   source_file: str, license_path: Path, *, ffmpeg: str='ffmpeg', ffprobe: str='ffprobe',
                   cwebp: str='cwebp', zip_path: Path|None=None, force: bool=False) -> dict[str,Any]:
    source=Path(source).absolute(); out_dir=Path(out_dir); license_path=Path(license_path)
    if not ID_RE.fullmatch(clip): raise ConversionError(f"clip id must be lowercase ASCII slug: {clip!r}")
    if not HEX40.fullmatch(source_commit): raise ConversionError('source_commit must be a 40-character git commit SHA')
    rel=PurePosixPath(source_file)
    if rel.is_absolute() or not source_file or any(p in ('','..','.') for p in rel.parts) or '\\' in source_file:
        raise ConversionError('source_file must be a safe repository-relative POSIX path')
    if not title.strip() or len(title)>512 or '\x00' in title: raise ConversionError('title must be 1..512 nonblank text characters')
    if source.is_symlink() or not source.is_file(): raise ConversionError('source must be an existing regular non-symlink file')
    if source.stat().st_size>64*1024*1024: raise ConversionError('source exceeds 64 MiB limit')
    if license_path.is_symlink() or not license_path.is_file(): raise ConversionError('license_path must be an existing regular file')
    out_dir=out_dir.absolute(); zip_path=Path(zip_path).absolute() if zip_path else out_dir.parent/f'{clip}-webp-frames-v1.zip'
    if (out_dir.is_symlink() or zip_path.is_symlink() or
        any(p.is_symlink() for p in (*out_dir.parents,*zip_path.parents))):
        raise ConversionError('refusing symlink output paths or ancestry')
    if out_dir.exists() and not out_dir.is_dir(): raise ConversionError('out_dir must be a directory')
    if zip_path.exists() and not zip_path.is_file(): raise ConversionError('ZIP destination must be a regular file')
    if zip_path==out_dir or out_dir in zip_path.parents or zip_path in out_dir.parents:
        raise ConversionError('ZIP and action directory must not overlap')
    if any(p==out_dir or out_dir in p.parents or p==zip_path for p in (source,license_path.absolute())):
        raise ConversionError('outputs must not overwrite source or license')
    source_hash=sha256_file(source); versions={"ffmpeg":tool_version(ffmpeg),"ffprobe":tool_version(ffprobe),"cwebp":tool_version(cwebp)}
    profile={"profile":PROFILE,"versions":versions,"converter_sha256":sha256_file(Path(__file__)),
             "license_sha256":sha256_file(license_path)}; profile_hash=hashlib.sha256(json.dumps(profile,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    state_path=out_dir.parent/'.converter-state'/f'{clip}.json'
    if not force and state_path.is_file():
        try:
            state=json.loads(state_path.read_text('utf-8'))
            if state.get('source_sha256')==source_hash and state.get('profile_sha256')==profile_hash and state.get('clip')==clip and out_dir.is_dir() and zip_path.is_file() and sha256_file(zip_path)==state.get('zip_sha256'):
                old=verify_action(out_dir,zip_path)
                expected_source={"repository":REPOSITORY,"commit":source_commit.lower(),"file":source_file,"sha256":source_hash}
                if (old['source']==expected_source and old.get('title')==title and old.get('clip')==clip
                    and (out_dir/'LICENSE.txt').read_bytes()==license_path.read_bytes()
                    and isinstance(state.get('source_probe'),dict)):
                    return {"manifest":old,"action_dir":str(out_dir),"zip":{"path":str(zip_path),"bytes":zip_path.stat().st_size,"sha256":state['zip_sha256']},"reused":True,"source_sha256":source_hash,"profile_sha256":profile_hash,"source_probe":state.get('source_probe'),"warnings":state.get('warnings',[])}
        except Exception: pass
    info=probe(source,ffprobe); count=sampling_count(Fraction(info['duration_ms'],1000)); durations=frame_durations(count,info['duration_ms'])
    if count>720: raise ConversionError(f"refusing unusually long source ({count} output frames); split the source explicitly")
    warnings=[]
    if (info['width'],info['height'])!=(640,360): raise ConversionError(f"source canvas requires review: {info['width']}x{info['height']}; expected 640x360")
    if info['source_fps']!=12: warnings.append(f"source rate is {info['source_fps']} fps; sampled at 12 fps")
    if info['source_frames'] is None: warnings.append('ffprobe could not count source frames')
    if len((license_path.read_bytes()))==0: raise ConversionError('license file is empty')
    try: license_path.read_text('utf-8')
    except UnicodeDecodeError as e: raise ConversionError('license file must be UTF-8') from e
    parent=out_dir.parent; parent.mkdir(parents=True,exist_ok=True)
    stage=Path(tempfile.mkdtemp(prefix=f'.{clip}.stage-',dir=parent)); stage_action=stage/'action'; stage_frames=stage_action/'frames'; stage_frames.mkdir(parents=True)
    alpha_min=255; alpha_max=0; produced=[]
    try:
        pngdir=stage/'png'; pngdir.mkdir()
        pattern=str(pngdir/'frame-%06d.png')
        run([ffmpeg,'-nostdin','-hide_banner','-loglevel','error','-y','-protocol_whitelist','file,pipe','-c:v','libvpx-vp9','-i',str(source),'-map','0:v:0','-an','-sn','-dn','-vf','tpad=stop_mode=clone:stop_duration=0.084,fps=12:start_time=0:round=near,format=rgba','-fps_mode','passthrough','-frames:v',str(count),'-pred','0','-threads','1','-start_number','0',pattern],timeout=3600)
        pngs=sorted(pngdir.glob('frame-*.png'))
        if len(pngs)!=count: raise ConversionError(f"sampling yielded {len(pngs)} frames; expected normalized 12fps count={count}; no package emitted")
        entries=[]
        for i,png in enumerate(pngs):
            w,h,amin,amax=png_rgba_alpha(png)
            if (w,h)!=(info['width'],info['height']): raise ConversionError(f"frame {i} canvas changed from source dimensions")
            if amax==0: raise ConversionError(f"frame {i} is entirely transparent; refusing invisible output")
            alpha_min=min(alpha_min,amin); alpha_max=max(alpha_max,amax)
            relname=f'frames/{i:03d}.webp'; dest=stage_action/relname
            run([cwebp,'-quiet','-q','85','-alpha_q','100','-m','4','-metadata','none','-exact',str(png),'-o',str(dest)],timeout=300)
            b=dest.stat().st_size
            entries.append({"file":relname,"bytes":b,"sha256":sha256_file(dest),"durationMs":durations[i]})
            produced.append(relname)
        alpha_present=alpha_min<255
        if not alpha_present: raise ConversionError('decoded source is fully opaque; alpha was lost or absent')
        if alpha_min not in (0,255) or alpha_max not in (0,255): warnings.append(f"sampled alpha range is {alpha_min}..{alpha_max}")
        manifest={"schema":1,"clip":clip,"title":title,"width":info['width'],"height":info['height'],"frameCount":count,"durationMs":sum(durations),
          "source":{"repository":REPOSITORY,"commit":source_commit.lower(),"file":source_file,"sha256":source_hash},
          "license":LICENSE_ID,"frames":entries}
        # Stable UTF-8 JSON, no timestamps or machine-specific paths.
        (stage_action/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8',newline='\n')
        shutil.copyfile(license_path,stage_action/'LICENSE.txt')
        members=['manifest.json','LICENSE.txt']+produced
        stage_zip=stage/f'{clip}-webp-frames-v1.zip'; _zip_create(stage_action,stage_zip,members)
        compressed=stage_zip.stat().st_size; unpacked=sum((stage_action/n).stat().st_size for n in members)
        if compressed>MAX_PACKAGE or unpacked>MAX_PACKAGE:
            raise ConversionError(f"package exceeds 8 MiB (ZIP={compressed} bytes, unpacked={unpacked} bytes); quality was not reduced")
        # Validate staged outputs before touching any existing generation.
        verify_action(stage_action,stage_zip)
        history=None
        if out_dir.exists() or zip_path.exists():
            old_hash=hashlib.sha256()
            for p in (out_dir/'manifest.json',zip_path):
                if p.is_file() and not p.is_symlink(): old_hash.update(p.read_bytes())
            history_root=parent/'.history'/clip; history_root.mkdir(parents=True,exist_ok=True)
            history=history_root/old_hash.hexdigest()[:16]
            n=1
            while history.exists(): history=history_root/f'{old_hash.hexdigest()[:16]}-{n}'; n+=1
            history.mkdir()
            try:
                if out_dir.exists(): os.replace(out_dir,history/'action')
                if zip_path.exists(): os.replace(zip_path,history/zip_path.name)
            except Exception:
                if (history/'action').exists(): os.replace(history/'action',out_dir)
                raise
        out_dir.parent.mkdir(parents=True,exist_ok=True); zip_path.parent.mkdir(parents=True,exist_ok=True)
        installed_action=False; installed_zip=False
        try:
            os.replace(stage_action,out_dir); installed_action=True
            os.replace(stage_zip,zip_path); installed_zip=True
        except Exception:
            if installed_action: _remove_owned_tree(out_dir)
            if installed_zip: zip_path.unlink()
            if history:
                if (history/'action').exists(): os.replace(history/'action',out_dir)
                if (history/zip_path.name).exists(): os.replace(history/zip_path.name,zip_path)
            raise
        final=verify_action(out_dir,zip_path); zip_hash=sha256_file(zip_path)
        state={"clip":clip,"source_sha256":source_hash,"source_commit":source_commit.lower(),"source_file":source_file,
          "profile_sha256":profile_hash,"profile":profile,"zip_sha256":zip_hash,
          "source_probe":{"width":info['width'],"height":info['height'],"duration_numerator":info['duration'].numerator,"duration_denominator":info['duration'].denominator,
            "duration_ms":info['duration_ms'],"source_frames":info['source_frames'],"source_fps":str(info['source_fps']),"codec":info['codec'],"pixel_format":info['pixel_format'],"alpha_min":alpha_min,"alpha_max":alpha_max},"warnings":warnings}
        _atomic_json(state_path,state)
        return {"manifest":final,"action_dir":str(out_dir),"zip":{"path":str(zip_path),"bytes":zip_path.stat().st_size,"sha256":zip_hash,"uncompressed_bytes":unpacked},
          "reused":False,"source_sha256":source_hash,"profile_sha256":profile_hash,"source_probe":state['source_probe'],"warnings":warnings,"history":str(history) if history else None}
    finally:
        _remove_owned_tree(stage,ignore_errors=True)

def main(argv: list[str]|None=None) -> int:
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--source',type=Path,required=True); ap.add_argument('--out-dir',type=Path,required=True,help='output action directory, e.g. repo/actions/<clip>')
    ap.add_argument('--clip'); ap.add_argument('--title',required=True); ap.add_argument('--source-commit',required=True); ap.add_argument('--source-file',required=True); ap.add_argument('--license',type=Path,required=True)
    ap.add_argument('--id-map',type=Path,help='JSON object mapping exact source_file paths to stable clip IDs'); ap.add_argument('--ffmpeg',default='ffmpeg'); ap.add_argument('--ffprobe',default='ffprobe'); ap.add_argument('--cwebp',default='cwebp'); ap.add_argument('--zip-out',type=Path); ap.add_argument('--force',action='store_true')
    ns=ap.parse_args(argv)
    try:
        mapping=json.loads(ns.id_map.read_text('utf-8')) if ns.id_map else None
        if mapping is not None and not isinstance(mapping,dict): raise ConversionError('--id-map must contain a JSON object')
        clip=ns.clip or stable_clip_id(ns.title,ns.source_file,mapping)
        result=convert_action(ns.source,ns.out_dir,clip,ns.title,ns.source_commit,ns.source_file,ns.license,ffmpeg=ns.ffmpeg,ffprobe=ns.ffprobe,cwebp=ns.cwebp,zip_path=ns.zip_out,force=ns.force)
        print(json.dumps(result,ensure_ascii=False,indent=2)); return 0
    except (ConversionError,OSError,ValueError,KeyError) as e:
        print(f'convert_action: {e}',file=sys.stderr); return 2

if __name__=='__main__': raise SystemExit(main())
