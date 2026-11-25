"""Real orchestration ordering with synthetic local converter outputs; no network."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('batch_sync', ROOT / 'scripts/sync_upstream.py')
sync = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sync)

class BatchPublishGate(unittest.TestCase):
    def run_batch(self, *, fail_second=False, publish=True):
        events = []
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for name, data in [('LICENSE.txt', 'approved text'), ('sync-policy.json','{}'),
                               ('action-map.json','{"a":{"id":"a"},"b":{"id":"b"}}'), ('catalog.json','{"actions":[]}')]:
                (root/name).write_text(data)
            entries = [{'path':sync.PREFIX + x + '.webm', 'sha':sync.git_blob(x.encode()),
                        'size':1, 'mode':'100644', 'type':'blob'} for x in ('a','b')]
            def source_bytes(commit, entry):
                if fail_second and entry['path'].endswith('/b.webm'):
                    raise RuntimeError('synthetic later-source failure')
                return Path(entry['path']).stem.encode()
            def converter(cmd, **kwargs):
                get = lambda key: cmd[cmd.index(key)+1]
                clip = get('--clip'); output = Path(get('--out-dir'))
                (output/'frames').mkdir(parents=True)
                (output/'frames/000.webp').write_bytes(b'synthetic')
                (output/'manifest.json').write_text('{}')
                (output/'LICENSE.txt').write_text('approved text')
                package = output.parent/(clip+'.zip'); package.write_bytes(clip.encode())
                events.append('convert:'+clip)
                payload = {'zip':{'path':str(package)}, 'source_probe':{
                    'duration_ms':83, 'alpha_min':0, 'alpha_max':255}}
                return subprocess.CompletedProcess(cmd,0,json.dumps(payload),'')
            def manifest(output, package, **kwargs):
                return {'width':640,'height':360,'frameCount':1,'frames':[{'durationMs':83}],
                        'source':{'repository':sync.SOURCE_URL,'file':kwargs['source_file'],
                                  'commit':kwargs['commit'],'sha256':kwargs['source_sha']}}
            def release(package, tag, title, **kwargs):
                events.append('release:'+tag.split('-12fps-')[0])
            argv = ['sync_upstream.py'] + (['--publish'] if publish else [])
            with patch.dict(os.environ, {}, clear=True), patch.object(sync,'ROOT',root), \
                 patch('sys.argv',argv), patch.object(sync,'pinned_tree',return_value=('a'*40,{x['path']:x for x in entries})), \
                 patch.object(sync,'check_licenses'), patch.object(sync,'source_bytes',side_effect=source_bytes), \
                 patch.object(sync.validation,'check_legacy',return_value={}), \
                 patch.object(sync.validation,'recipe',return_value='b'*64), \
                 patch.object(sync.validation,'validate_catalog'), \
                 patch.object(sync.validation,'validated_output',side_effect=manifest), \
                 patch.object(sync,'independent_source_duration',return_value=83), \
                 patch.object(sync.subprocess,'run',side_effect=converter), \
                 patch.object(sync,'release',side_effect=release), patch('builtins.print'):
                if fail_second:
                    with self.assertRaisesRegex(RuntimeError,'later-source failure'):
                        sync.main()
                    self.assertEqual(json.loads((root/'catalog.json').read_text()), {'actions':[]})
                    self.assertFalse((root/'actions').exists())
                else:
                    sync.main()
                    target = root/('catalog.json' if publish else 'sync-preview.json')
                    self.assertEqual(len(json.loads(target.read_text())['actions']),2)
                    if not publish:
                        self.assertFalse((root/'actions').exists())
        return events

    def test_later_failure_publishes_nothing(self):
        self.assertEqual(self.run_batch(fail_second=True), ['convert:a'])

    def test_all_conversions_precede_first_release(self):
        self.assertEqual(self.run_batch(), ['convert:a','convert:b','release:a','release:b'])

    def test_preview_never_releases_or_installs(self):
        self.assertEqual(self.run_batch(publish=False), ['convert:a','convert:b'])

if __name__ == '__main__':
    unittest.main()
