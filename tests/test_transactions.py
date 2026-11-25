"""Mocked codec tests cover invalidation and install failure without binaries."""
import json, struct, sys, zlib
from fractions import Fraction
from pathlib import Path
from unittest.mock import patch
from test_converter import TempCase
import convert_action as c

def chunk(kind,data): return struct.pack('>I',len(data))+kind+data+struct.pack('>I',zlib.crc32(kind+data)&0xffffffff)
def png():
    row=b'\0'+bytes([10,20,30,0])*320+bytes([10,20,30,255])*320
    return b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',640,360,8,6,0,0,0))+chunk(b'IDAT',zlib.compress(row*360))+chunk(b'IEND',b'')
def fake_webp():
    # Deliberately structural mock, not a decodable image. Real independent
    # decoding is exercised by run_integration.py, not these transaction tests.
    def part(k,d): return k+struct.pack('<I',len(d))+d+(b'\0' if len(d)%2 else b'')
    body=b'WEBP'+part(b'VP8X',b'\x10\0\0\0'+(639).to_bytes(3,'little')+(359).to_bytes(3,'little'))+part(b'ALPH',b'\0')+part(b'VP8 ',b'\0\0\0\x9d\x01\x2a'+struct.pack('<HH',640,360))
    return b'RIFF'+struct.pack('<I',len(body))+body

class CacheAndTransactionTests(TempCase):
    def setUp(self):
        super().setUp()
        self.source=self.root/'source.webm'; self.source.write_bytes(b'stable-source')
        self.license=self.root/'approved-license.txt'; self.license.write_text('approved attribution')
        self.args=[self.source,self.root/'action','test-action','Title','a'*40,'assets/a.webm',self.license]
        self.calls=0
        def codec(args,timeout=1800):
            if args[0]=='ffmpeg':
                self.calls+=1; Path(args[-1].replace('%06d','000000')).write_bytes(png())
            elif args[0]=='cwebp': Path(args[-1]).write_bytes(fake_webp())
            else: raise AssertionError(args)
        for name,value in [('tool_version',lambda cmd:'pinned-'+cmd),('probe',lambda *a:{'width':640,'height':360,'duration':Fraction(1,12),'duration_ms':83,'source_frames':1,'source_fps':Fraction(12),'codec':'vp9','pixel_format':'yuv420p'}),('run',codec)]:
            p=patch.object(c,name,side_effect=value); p.start(); self.addCleanup(p.stop)
    def build(self,**kw): return c.convert_action(*self.args,**kw)
    def test_unchanged_reuses_valid_package(self):
        a=self.build(); b=self.build(); self.assertFalse(a['reused']); self.assertTrue(b['reused']); self.assertEqual(self.calls,1); self.assertEqual(a['zip']['sha256'],b['zip']['sha256'])
    def test_force_rebuild_is_byte_identical_preserves_history(self):
        a=self.build(); b=self.build(force=True); self.assertEqual(self.calls,2); self.assertEqual(a['zip']['sha256'],b['zip']['sha256']); self.assertTrue((Path(b['history'])/'action/manifest.json').is_file())
    def test_title_invalidates_cache(self):
        self.build(); self.args[3]='New title'; b=self.build(); self.assertFalse(b['reused']); self.assertEqual(b['manifest']['title'],'New title')
    def test_commit_invalidates_cache(self):
        self.build(); self.args[4]='b'*40; b=self.build(); self.assertFalse(b['reused']); self.assertEqual(b['manifest']['source']['commit'],'b'*40)
    def test_source_path_invalidates_cache(self):
        self.build(); self.args[5]='assets/renamed.webm'; b=self.build(); self.assertFalse(b['reused']); self.assertEqual(b['manifest']['source']['file'],'assets/renamed.webm')
    def test_license_invalidates_cache(self):
        a=self.build(); self.license.write_text('approved updated attribution'); b=self.build(); self.assertFalse(b['reused']); self.assertNotEqual(a['zip']['sha256'],b['zip']['sha256']); self.assertEqual((Path(b['action_dir'])/'LICENSE.txt').read_text(),'approved updated attribution')
    def test_corrupt_output_rebuilt(self):
        a=self.build(); (Path(a['action_dir'])/'frames/000.webp').write_bytes(b'corrupt'); b=self.build(); self.assertFalse(b['reused']); self.assertEqual(a['zip']['sha256'],b['zip']['sha256'])
    def test_zip_install_failure_restores_old_generation(self):
        a=self.build(); old_manifest=(self.args[1]/'manifest.json').read_bytes(); old_zip=Path(a['zip']['path']).read_bytes(); real=c.os.replace
        def fail(src,dst):
            if Path(src).name.endswith('.zip') and '.stage-' in str(src): raise OSError('simulated install failure')
            return real(src,dst)
        self.args[3]='New title'
        with patch.object(c.os,'replace',side_effect=fail): self.assertRaises(OSError,self.build)
        self.assertEqual((self.args[1]/'manifest.json').read_bytes(),old_manifest); self.assertEqual(Path(a['zip']['path']).read_bytes(),old_zip)
    def test_zip_backup_failure_restores_old_directory(self):
        a=self.build(); old_manifest=(self.args[1]/'manifest.json').read_bytes(); real=c.os.replace
        def fail(src,dst):
            if Path(src)==Path(a['zip']['path']): raise OSError('simulated backup failure')
            return real(src,dst)
        self.args[3]='New title'
        with patch.object(c.os,'replace',side_effect=fail): self.assertRaises(OSError,self.build)
        self.assertEqual((self.args[1]/'manifest.json').read_bytes(),old_manifest); self.assertEqual(c.sha256_file(Path(a['zip']['path'])),a['zip']['sha256'])
    def test_no_private_stages_left(self):
        self.build(); self.assertEqual(list(self.root.glob('.*.stage-*')),[])
    def test_invalid_timing_rejected(self):
        a=self.build(); p=Path(a['action_dir'])/'manifest.json'; m=json.loads(p.read_text()); m['frames'][0]['durationMs']=100; m['durationMs']=100; p.write_text(json.dumps(m)); self.assertRaises(c.ConversionError,c.verify_action,Path(a['action_dir']))
    def test_missing_requested_zip_rejected(self):
        a=self.build(); self.assertRaises(c.ConversionError,c.verify_action,Path(a['action_dir']),self.root/'missing.zip')
    def test_license_symlink_rejected(self):
        a=self.build(); p=Path(a['action_dir'])/'LICENSE.txt'; p.unlink(); p.symlink_to(self.license); self.assertRaises(c.ConversionError,c.verify_action,Path(a['action_dir']))
    def test_manifest_symlink_rejected(self):
        a=self.build(); p=Path(a['action_dir'])/'manifest.json'; q=self.root/'saved-manifest.json'; q.write_bytes(p.read_bytes()); p.unlink(); p.symlink_to(q); self.assertRaises(c.ConversionError,c.verify_action,Path(a['action_dir']))
    def test_boolean_duration_rejected(self):
        a=self.build(); p=Path(a['action_dir'])/'manifest.json'; m=json.loads(p.read_text()); m['frames'][0]['durationMs']=True; m['durationMs']=1; p.write_text(json.dumps(m)); self.assertRaises(c.ConversionError,c.verify_action,Path(a['action_dir']))
