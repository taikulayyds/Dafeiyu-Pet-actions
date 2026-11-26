"""Scoped standardization must not alter unrelated resources or restore v1."""
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('scope_sync', ROOT/'scripts/sync_upstream.py')
sync = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sync)

class ScopedSafeguards(unittest.TestCase):
    def setUp(self):
        self.entry = {'sha': 'a'*40}
        self.previous = {'source': {'gitBlob': 'a'*40}, 'title': 'Other',
                         'profile': sync.PROFILE, 'recipe': 'old-toolchain', 'status': 'active'}

    def test_other_action_keeps_existing_toolchain_and_every_field(self):
        import copy
        before = copy.deepcopy(self.previous)
        self.assertTrue(sync.scoped_unchanged(self.previous, self.entry, 'Other'))
        self.assertEqual(before, self.previous)

    def test_new_or_changed_other_action_fails_closed(self):
        for previous, entry, title in [(None, self.entry, 'Other'),
                (self.previous, {'sha': 'b'*40}, 'Other'),
                (self.previous, self.entry, 'Changed')]:
            with self.subTest(previous=previous,entry=entry,title=title), self.assertRaises(RuntimeError):
                sync.scoped_unchanged(previous, entry, title)

    def test_nonstandard_or_missing_other_action_not_silently_ignored(self):
        for key, value in [('profile','v1'), ('status','missing-upstream')]:
            previous = dict(self.previous, **{key:value})
            with self.subTest(key=key), self.assertRaises(RuntimeError):
                sync.scoped_unchanged(previous, self.entry, 'Other')

    def test_no_v1_short_circuit_in_conversion_loop(self):
        code = (ROOT/'scripts/sync_upstream.py').read_text()
        self.assertNotIn('if is_legacy', code)
        self.assertIn('Real source double-build ZIP is not reproducible', code)

    def test_scope_cannot_select_arbitrary_clip(self):
        with patch('sys.argv',['sync_upstream.py','--only-action','other']):
            with self.assertRaises(SystemExit) as result:
                sync.main()
            self.assertNotEqual(result.exception.code,0)

if __name__ == '__main__':
    unittest.main()


class ScopedOrchestration(unittest.TestCase):
    def run_scope(self, *, mismatch=False, other_changed=False, add_source=False):
        import copy
        import hashlib
        import json
        import os
        import subprocess
        import tempfile
        events = []
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            song = sync.PREFIX+'song.webm'
            other = sync.PREFIX+'other.webm'
            entries = [{'path':path,'sha':sync.git_blob(path.encode()),'size':1,
                        'mode':'100644','type':'blob'} for path in (other,song)]
            target = {'clip':'youxian-hengga','title':'Song','releaseTag':'youxian-hengga-v1',
                'package':{'sha256':'1719e2260ab889844c9f6ac271c133b1d11ed91087002627960d74146e08d256'},
                'source':{'gitBlob':entries[1]['sha']}}
            kept = {'clip':'other','title':'Other','profile':sync.PROFILE,'recipe':'old-toolchain',
                    'source':{'gitBlob':entries[0]['sha']},'status':'active',
                    'frameCount':1,'package':{'bytes':1},'untouched':{'nested':[1,2]}}
            if other_changed:
                entries[0]['sha'] = 'f'*40
            if add_source:
                entries.append(dict(entries[0],path=sync.PREFIX+'new.webm'))
            originals = copy.deepcopy(kept)
            (root/'LICENSE.txt').write_text('approved')
            (root/'sync-policy.json').write_text('{}')
            (root/'action-map.json').write_text(json.dumps({'song':{'id':'youxian-hengga','title':'Song'},
                                                          'other':{'id':'other','title':'Other'}}))
            (root/'catalog.json').write_text(json.dumps({'actions':[kept,target]}))
            def convert(cmd,**kwargs):
                out = Path(cmd[cmd.index('--out-dir')+1])
                (out/'frames').mkdir(parents=True)
                (out/'frames/000.webp').write_bytes(b'synthetic')
                (out/'manifest.json').write_text('{}')
                (out/'LICENSE.txt').write_text('approved')
                package = out.parent/'song.zip'
                package.write_bytes(b'changed' if mismatch and 'repeat' in out.parts else b'identical')
                events.append('convert')
                return subprocess.CompletedProcess(cmd,0,json.dumps({'zip':{'path':str(package)},
                    'source_probe':{'duration_ms':83,'alpha_min':0,'alpha_max':255}}),'')
            def manifest(out,package,**kwargs):
                return {'width':640,'height':360,'frameCount':1,'frames':[{'durationMs':83}],
                        'source':{'repository':sync.SOURCE_URL,'commit':kwargs['commit'],
                                  'file':kwargs['source_file'],'sha256':kwargs['source_sha']}}
            with patch.dict(os.environ,{},clear=True),patch.object(sync,'ROOT',root), \
                    patch('sys.argv',['sync_upstream.py','--publish','--only-action','youxian-hengga']), \
                    patch.object(sync,'pinned_tree',return_value=('a'*40,{x['path']:x for x in entries})), \
                    patch.object(sync,'check_licenses'),patch.object(sync.validation,'check_legacy',return_value={}), \
                    patch.object(sync.validation,'recipe',return_value='b'*64), \
                    patch.object(sync.validation,'validate_catalog'),patch.object(sync.validation,'validated_output',side_effect=manifest), \
                    patch.object(sync,'source_bytes',side_effect=lambda commit,entry:entry['path'].encode()), \
                    patch.object(sync,'independent_source_duration',return_value=83), \
                    patch.object(sync.subprocess,'run',side_effect=convert), \
                    patch.object(sync,'release',side_effect=lambda *a,**k:events.append('release')),patch('builtins.print'):
                if mismatch or other_changed or add_source:
                    with self.assertRaises(RuntimeError):
                        sync.main()
                    self.assertNotIn('release',events)
                    self.assertEqual(json.loads((root/'catalog.json').read_text())['actions'],[originals,target])
                else:
                    sync.main()
                    result = json.loads((root/'catalog.json').read_text())
                    actual = next(x for x in result['actions'] if x['clip']=='other')
                    self.assertEqual(actual,originals)
                    updated = next(x for x in result['actions'] if x['clip']=='youxian-hengga')
                    self.assertEqual(updated['profile'],sync.PROFILE)
                    self.assertNotEqual(updated['releaseTag'],'youxian-hengga-v1')
        return events

    def test_only_song_converts_twice_and_others_unchanged(self):
        self.assertEqual(self.run_scope(),['convert','convert','release'])

    def test_real_repeat_mismatch_publishes_nothing(self):
        self.assertEqual(self.run_scope(mismatch=True),['convert','convert'])

    def test_unselected_source_changed_publishes_nothing(self):
        self.assertEqual(self.run_scope(other_changed=True),[])

    def test_added_source_requires_full_sync(self):
        self.assertEqual(self.run_scope(add_source=True),[])
