"""Fast contract tests; all temporary writes stay below converter/tests/."""
import contextlib, importlib.util, io, json, os, struct, sys, tempfile, unittest, zipfile, zlib
from fractions import Fraction
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import convert_action as c

class TempCase(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.mkdtemp(dir=ROOT/'tests')
        self.root=Path(self.tmp)
        self.addCleanup(self.cleanup)
    def cleanup(self):
        # iSH shared mounts report differing stat/fstat st_dev, so Python 3.12
        # fd-based rmtree mistakes real directories for symlinks.
        for base,dirs,files in os.walk(self.root,topdown=False,followlinks=False):
            for name in files: (Path(base)/name).unlink()
            for name in dirs:
                p=Path(base)/name
                if p.is_symlink(): p.unlink()
                else: p.rmdir()
        self.root.rmdir()

class TimingTests(unittest.TestCase):
    def test_all_114_source_durations(self):
        counts=[]
        for ms in [10042]*110+[5125]*3+[4917]:
            n=c.sampling_count(Fraction(ms,1000)); ds=c.frame_durations(n,ms)
            counts.append(n)
            self.assertEqual(sum(ds),ms)
            self.assertTrue(all(x>0 for x in ds))
            for i,duration in enumerate(ds):
                delta=((i+1)*1000+6)//12-(i*1000+6)//12
                if i<n-1: self.assertEqual(duration,delta)
                else: self.assertLessEqual(duration,delta)
        self.assertEqual(counts,[121]*110+[62]*3+[59])
        self.assertEqual(sum(counts),13555)
    def test_matches_readonly_main_validator_counts(self):
        path=Path(os.environ.get('CONVERTER_MAIN_REPO',str(ROOT)))/'scripts/sync_validation.py'
        self.assertTrue(path.is_file(), 'main validator must be present for the contract check')
        spec=importlib.util.spec_from_file_location('readonly_main_validation',path)
        v=importlib.util.module_from_spec(spec); spec.loader.exec_module(v)
        for ms in [10042]*110+[5125]*3+[4917]+[1,41,42,83,84,125,166,167,250,333,334,499,500,501,60000]:
            self.assertEqual(c.sampling_count(Fraction(ms,1000)),v.sampling_count(ms))
    def test_expected_tail_durations(self):
        for ms,n,tail in [(10042,121,42),(5125,62,42),(4917,59,84)]:
            self.assertEqual(c.sampling_count(Fraction(ms,1000)),n)
            self.assertEqual(c.frame_durations(n,ms)[-1],tail)
    def test_snap_half_millisecond_inclusive(self):
        tick=Fraction(59,12)
        for delta in [Fraction(-1,2000),Fraction(0),Fraction(1,3000),Fraction(1,2000)]:
            self.assertEqual(c.sampling_count(tick+delta),59)
        self.assertEqual(c.sampling_count(tick+Fraction(501,1000000)),60)
    def test_no_zero_duration_tail(self):
        self.assertRaises(c.ConversionError,c.frame_durations,60,4917)
    def test_83_84_grid(self):
        self.assertEqual(c.frame_durations(12,1000),[83,84,83]*4)
    def test_duration_bounds(self):
        for args in [(0,1),(1,0),(2,1)]: self.assertRaises(c.ConversionError,c.frame_durations,*args)

class IdentityTests(unittest.TestCase):
    def test_legacy_id(self): self.assertEqual(c.stable_clip_id('悠闲哼歌','dsh-pet/assets/webm/悠闲哼歌.webm'),'youxian-hengga')
    def test_mapping_override(self): self.assertEqual(c.stable_clip_id('题','a.webm',{'a.webm':'explicit-id'}),'explicit-id')
    def test_mapping_reject(self): self.assertRaises(c.ConversionError,c.stable_clip_id,'题','a',{'a':'../escape'})
    def test_stable_unicode_id(self):
        a=c.stable_clip_id('题','a.webm'); self.assertEqual(a,c.stable_clip_id('题','a.webm')); self.assertNotEqual(a,c.stable_clip_id('题','b.webm'))
    def test_clean_environment(self):
        with patch.dict(os.environ,{'SECRET_TEST':'not-a-secret','LD_PRELOAD':'bad','HTTPS_PROXY':'bad','PYTHONPATH':'bad'},clear=False):
            env=c.clean_environment()
            for k in ['SECRET_TEST','LD_PRELOAD','HTTPS_PROXY','PYTHONPATH']: self.assertNotIn(k,env)
    def test_run_passes_clean_environment(self):
        with patch.object(c.subprocess,'run') as p:
            c.run(['fake']); self.assertFalse(p.call_args.kwargs['shell']); self.assertEqual(p.call_args.kwargs['env'],c.clean_environment())

class ArchiveTests(TempCase):
    def test_zip_determinism_under_mtime_mode_order(self):
        action=self.root/'action'; (action/'frames').mkdir(parents=True)
        members=['manifest.json','LICENSE.txt','frames/000.webp']
        for n in reversed(members): (action/n).write_bytes(n.encode())
        a=self.root/'a.zip'; b=self.root/'b.zip'
        c._zip_create(action,a,members)
        for n in members: os.utime(action/n,(1,1788888888)); (action/n).chmod(0o600)
        c._zip_create(action,b,members)
        self.assertEqual(a.read_bytes(),b.read_bytes())
        with zipfile.ZipFile(a) as z:
            self.assertEqual(z.namelist(),members)
            for i in z.infolist():
                self.assertEqual(i.date_time,(1980,1,1,0,0,0)); self.assertEqual(i.extra,b''); self.assertEqual(i.comment,b''); self.assertEqual(i.external_attr>>16,0o100644); self.assertEqual(i.compress_type,8)
    def test_unsafe_zip_paths(self):
        for name in ['../a','/a','a//b','a/./b','a\\b','']:
            with self.subTest(name=name): self.assertRaises(c.ConversionError,c._zip_create,self.root,self.root/'a.zip',[name])
    def test_duplicate_members(self): self.assertRaises(c.ConversionError,c._zip_create,self.root,self.root/'a.zip',['x','x'])
    def test_symlink_member(self):
        (self.root/'x').symlink_to('/not-used'); self.assertRaises(c.ConversionError,c._zip_create,self.root,self.root/'a.zip',['x'])
    def test_empty_riff_rejected(self):
        p=self.root/'bad.webp'; p.write_bytes(b'RIFF'+struct.pack('<I',4)+b'WEBP'); self.assertRaises(c.ConversionError,c._webp_info,p)
    def test_fake_webp_rejected(self):
        p=self.root/'bad.webp'; p.write_bytes(b'not webp'); self.assertRaises(c.ConversionError,c._webp_info,p)

class PngTests(TempCase):
    def write_png(self,filters):
        # Five rows of identical RGBA pixels, encoded using each PNG predictor.
        row=bytes([10,20,30,0,40,50,60,128,70,80,90,255]); prev=bytes(len(row)); raw=b''
        for ft in filters:
            filtered=bytearray()
            for i,value in enumerate(row):
                a=row[i-4] if i>=4 else 0; b=prev[i]; cc=prev[i-4] if i>=4 else 0
                if ft==0: pred=0
                elif ft==1: pred=a
                elif ft==2: pred=b
                elif ft==3: pred=(a+b)//2
                else:
                    p=a+b-cc; ds=[abs(p-a),abs(p-b),abs(p-cc)]; pred=[a,b,cc][ds.index(min(ds))]
                filtered.append((value-pred)&255)
            raw+=bytes([ft])+filtered; prev=row
        def chunk(k,d): return struct.pack('>I',len(d))+k+d+struct.pack('>I',zlib.crc32(k+d)&0xffffffff)
        data=b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',3,len(filters),8,6,0,0,0))+chunk(b'IDAT',zlib.compress(raw))+chunk(b'IEND',b'')
        p=self.root/'alpha.png'; p.write_bytes(data); return p
    def test_none_fast_path(self): self.assertEqual(c.png_rgba_alpha(self.write_png([0,0])),(3,2,0,255))
    def test_all_filters(self): self.assertEqual(c.png_rgba_alpha(self.write_png([0,1,2,3,4])),(3,5,0,255))
    def test_bad_png(self):
        p=self.root/'bad.png'; p.write_bytes(b'bad'); self.assertRaises(c.ConversionError,c.png_rgba_alpha,p)

class CliAndSafetyTests(TempCase):
    def base(self):
        source=self.root/'s.webm'; source.write_bytes(b'source'); license=self.root/'LICENSE'; license.write_text('approved')
        return [source,self.root/'out','test','Title','a'*40,'dsh-pet/assets/webm/a.webm',license]
    def test_cli_sync_call_and_json(self):
        args=self.base()
        argv=['--source',str(args[0]),'--out-dir',str(args[1]),'--clip',args[2],'--title',args[3],'--source-commit',args[4],'--source-file',args[5],'--license',str(args[6])]
        response={'zip':{'path':'x.zip'},'source_probe':{'duration_ms':4917},'manifest':{}}
        with patch.object(c,'convert_action',return_value=response) as mock,contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(c.main(argv),0)
        self.assertEqual(json.loads(out.getvalue()),response); self.assertEqual(mock.call_args.args,tuple(args))
    def test_invalid_source_paths(self):
        for name in ['/absolute','../escape','a/../b','a\\b']:
            args=self.base(); args[5]=name; self.assertRaises(c.ConversionError,c.convert_action,*args)
    def test_invalid_commit(self):
        args=self.base(); args[4]='main'; self.assertRaises(c.ConversionError,c.convert_action,*args)
    def test_source_symlink(self):
        args=self.base(); link=self.root/'linked.webm'; link.symlink_to(args[0]); args[0]=link; self.assertRaises(c.ConversionError,c.convert_action,*args)
    def test_overlap_rejected_before_tools(self):
        args=self.base()
        with patch.object(c,'tool_version') as mocked:
            self.assertRaises(c.ConversionError,c.convert_action,*args,zip_path=args[1]/'bad.zip')
            mocked.assert_not_called()
    def test_source_not_overwritten(self):
        args=self.base(); self.assertRaises(c.ConversionError,c.convert_action,*args,zip_path=args[0]); self.assertEqual(args[0].read_bytes(),b'source')

if __name__=='__main__': unittest.main()
