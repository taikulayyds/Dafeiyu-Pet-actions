#!/usr/bin/env python3
"""Opt-in real-codec integration evidence; writes only below converter/."""
import hashlib, importlib.util, json, os, shutil, subprocess, sys, time, zipfile
from pathlib import Path
from PIL import Image, ImageDraw
ROOT=Path(__file__).resolve().parents[1]
REPO=Path(os.environ.get('CONVERTER_MAIN_REPO','/var/minis/shared/dafeiyu-frame-import-20261125/dafeiyu-actions-repo'))
AUDIT=Path(os.environ.get('CONVERTER_AUDIT_DIR',str(ROOT.parent/'audit')))
SOURCES=Path(os.environ.get('CONVERTER_SOURCE_DIR','/tmp/dafeiyu-audit-videos'))
sys.dont_write_bytecode=True
sys.path.insert(0,str(ROOT/'scripts'))
import convert_action as c
WORK=ROOT/'evidence'
WORK.mkdir(exist_ok=True)
LICENSE=WORK/'approved-license-snapshot.txt'
# Freeze all content inputs: the parent can edit the read-only main repository
# concurrently. A changed LICENSE is an input change, not ZIP randomness.
if not LICENSE.exists(): shutil.copyfile(REPO/'LICENSE.txt',LICENSE)
FF=os.environ.get('CONVERTER_FFMPEG','/tmp/dfy-ffmpeg')
FP=os.environ.get('CONVERTER_FFPROBE','/tmp/dfy-ffprobe')
COMMIT='fb38ce620576f5ed3735a99536d2b1a4a9eb313f'
spec=importlib.util.spec_from_file_location('validation',REPO/'scripts/sync_validation.py')
v=importlib.util.module_from_spec(spec); spec.loader.exec_module(v)
report={'versions':{x:c.tool_version(y) for x,y in [('ffmpeg',FF),('ffprobe',FP),('cwebp','cwebp')]},'cases':[]}
started=time.monotonic()
def command(args):
    return c.run([str(x) for x in args],timeout=600)
def independent(result,commit,file):
    m=v.validated_output(result['action_dir'],result['zip']['path'],clip=result['manifest']['clip'],title=result['manifest']['title'],commit=commit,source_file=file,source_sha=result['source_sha256'],expected_duration_ms=result['source_probe']['duration_ms'],expected_license_bytes=LICENSE.read_bytes())
    assert m==result['manifest']
    with zipfile.ZipFile(result['zip']['path']) as z:
        assert all(i.date_time==(1980,1,1,0,0,0) and i.extra==b'' and i.comment==b'' and i.create_system==3 and i.external_attr>>16==0o100644 for i in z.infolist())
    return m

def build(source,clip,title,file,folder):
    cmd=[sys.executable,ROOT/'scripts/convert_action.py','--source',source,'--out-dir',WORK/folder/clip,'--clip',clip,'--title',title,'--source-commit',COMMIT,'--source-file',file,'--license',LICENSE,'--ffmpeg',FF,'--ffprobe',FP,'--force']
    result=json.loads(command(cmd).stdout)
    independent(result,COMMIT,file)
    print('validated',folder,clip,result['zip']['sha256'],flush=True)
    return result

im=Image.new('RGBA',(640,360),(12,34,56,0)); d=ImageDraw.Draw(im)
d.rectangle((160,90,479,269),fill=(235,45,75,255)); d.rectangle((240,135,399,224),fill=(15,220,100,128))
im.save(WORK/'alpha-input.png')
command([FF,'-nostdin','-hide_banner','-loglevel','error','-y','-loop','1','-framerate','24','-i',WORK/'alpha-input.png','-frames:v','12','-c:v','libvpx-vp9','-pix_fmt','yuva420p','-lossless','1','-threads','1','-auto-alt-ref','0',WORK/'alpha-short.webm'])
command([FF,'-nostdin','-hide_banner','-loglevel','error','-y','-c:v','libvpx-vp9','-i',WORK/'alpha-short.webm','-frames:v','1','-pix_fmt','rgba',WORK/'alpha-decoded.png'])
with Image.open(WORK/'alpha-decoded.png') as decoded:
    source_alpha=decoded.getchannel('A').tobytes(); alpha_values=sorted(set(source_alpha))
    assert alpha_values[0]==0 and alpha_values[-1]==255 and any(0<x<255 for x in alpha_values)
short=build(WORK/'alpha-short.webm','alpha-test','透明测试','dsh-pet/assets/webm/alpha-short.webm','short')
for f in short['manifest']['frames']:
    with Image.open(Path(short['action_dir'])/f['file']) as frame:
        assert frame.getchannel('A').tobytes()==source_alpha
assert short['manifest']['frameCount']==6 and short['manifest']['durationMs']==500
report['cases'].append({'name':'synthetic-real-vp9-alpha','frames':6,'duration_ms':500,'alpha_values':alpha_values,'exact_alpha_all_frames':True,'zip_sha256':short['zip']['sha256']})

# All input pixels transparent is intentionally rejected, matching sync_validation.
Image.new('RGBA',(640,360),(0,0,0,0)).save(WORK/'invisible.png')
command([FF,'-nostdin','-hide_banner','-loglevel','error','-y','-loop','1','-framerate','12','-i',WORK/'invisible.png','-frames:v','1','-c:v','libvpx-vp9','-pix_fmt','yuva420p','-lossless','1','-threads','1','-auto-alt-ref','0',WORK/'invisible.webm'])
try:
    c.convert_action(WORK/'invisible.webm',WORK/'invisible-output','invisible','Invisible',COMMIT,'fixtures/invisible.webm',REPO/'LICENSE.txt',ffmpeg=FF,ffprobe=FP)
    raise AssertionError('accepted entirely invisible source')
except c.ConversionError as e:
    assert 'entirely transparent' in str(e)
assert not (WORK/'invisible-output').exists()
report['cases'].append({'name':'entirely-transparent','rejected':True,'no_output':True})

metadata=json.loads((AUDIT/'source-media-metadata.json').read_text())
for title in ['工作状态-垂头叹气冒汗','三球抛接']:
    filename='dsh-pet/assets/webm/'+title+'.webm'
    source=SOURCES/(title+'.webm')
    entry=next(x for x in metadata if x['path']==filename)
    assert c.sha256_file(source)==entry['sha256']
    assert hashlib.sha1(b'blob '+str(source.stat().st_size).encode()+b'\0'+source.read_bytes()).hexdigest()==entry['git_blob_sha1']
    clip='boundary-test' if '工作状态' in title else 'normal-test'
    a=build(source,clip,title,filename,clip+'-a')
    # Independent destination, source mtime perturbation is not needed: ZIP members
    # receive changed mtimes explicitly and are independently rearchived below.
    b=build(source,clip,title,filename,clip+'-b')
    assert not a['reused'] and not b['reused']
    assert Path(a['zip']['path']).read_bytes()==Path(b['zip']['path']).read_bytes()
    action=Path(a['action_dir']); members=['manifest.json','LICENSE.txt']+[f['file'] for f in a['manifest']['frames']]
    for index,name in enumerate(members): os.utime(action/name,(123456789+index,1788888888+index))
    changed=WORK/(clip+'-mtime.zip'); c._zip_create(action,changed,members)
    assert c.sha256_file(changed)==a['zip']['sha256']
    report['cases'].append({'name':title,'source_file':filename,'commit':COMMIT,'source_sha256':entry['sha256'],'git_blob_verified':True,'frames':a['manifest']['frameCount'],'duration_ms':a['manifest']['durationMs'],'last_duration_ms':a['manifest']['frames'][-1]['durationMs'],'alpha_range':[a['source_probe']['alpha_min'],a['source_probe']['alpha_max']],'zip_bytes':a['zip']['bytes'],'zip_sha256_a':a['zip']['sha256'],'zip_sha256_b':b['zip']['sha256'],'byte_identical':True,'mtime_perturbed_identical':True,'main_validator':'passed'})
    (WORK/'integration-results.partial.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
report['elapsed_seconds']=round(time.monotonic()-started,3)
report['status']='passed'
(WORK/'integration-results.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
print(json.dumps(report,ensure_ascii=False,indent=2),flush=True)
