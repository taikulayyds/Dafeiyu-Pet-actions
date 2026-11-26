"""Explicit v1 retirement cannot waive image, replacement, or historical guards."""
import hashlib,json,sys,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import sync_validation as v

class Retirement(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.revision='a'*16
        self.manifest=self.root/('actions/youxian-hengga/revisions/'+self.revision+'/manifest.json')
        self.manifest.parent.mkdir(parents=True)
        self.manifest.write_bytes(b'verified manifest fixture')
        self.record={'status':'retired','files':{'actions/youxian-hengga/frames/000.webp':'b'*64},
            'catalogEntry':{'clip':'youxian-hengga'},'replacement':{'clip':'youxian-hengga',
            'version':self.revision,'releaseTag':'youxian-hengga-12fps-'+self.revision,
            'packageSHA256':self.revision+'0'*48,'manifestSHA256':v.sha(self.manifest.read_bytes())}}
        self.save()

    def save(self):
        (self.root/'legacy-v1.json').write_text(json.dumps(self.record))

    def test_old_resources_absent_and_verified_replacement_present(self):
        self.assertEqual(v.check_legacy(self.root),self.record['catalogEntry'])

    def test_reintroduced_resource_or_symlink_rejected(self):
        p=self.root/'actions/youxian-hengga/frames/000.webp'
        p.parent.mkdir(parents=True)
        p.write_bytes(b'old')
        with self.assertRaisesRegex(RuntimeError,'reintroduced'):v.check_legacy(self.root)
        p.unlink();p.symlink_to(self.root/'missing')
        with self.assertRaises(RuntimeError):v.check_legacy(self.root)

    def test_missing_or_changed_replacement_rejected(self):
        self.manifest.write_bytes(b'tampered')
        with self.assertRaises(RuntimeError):v.check_legacy(self.root)
        self.manifest.unlink()
        with self.assertRaises(RuntimeError):v.check_legacy(self.root)

    def test_unverified_retirement_record_rejected(self):
        for key,value in [('version','../escape'),('packageSHA256','f'*64),('releaseTag','old')]:
            old=self.record['replacement'][key]
            self.record['replacement'][key]=value;self.save()
            with self.assertRaises(RuntimeError):v.check_legacy(self.root)
            self.record['replacement'][key]=old
        self.record['status']='unknown';self.save()
        with self.assertRaises(RuntimeError):v.check_legacy(self.root)
