import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('sync', Path(__file__).resolve().parents[1] / 'scripts/sync_upstream.py')
sync = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(sync)

class Safeguards(unittest.TestCase):
    def setUp(self):
        # Mock assertion diagnostics must never include the real shell environment.
        clean = patch.dict(sync.os.environ, {}, clear=True)
        clean.start()
        self.addCleanup(clean.stop)

    def test_git_blob(self):
        self.assertEqual(sync.git_blob(b'hello\n'), 'ce013625030ba8dba906f756967f9e9ca394464a')

    def test_stable_id_known(self):
        self.assertEqual(sync.stable_id(sync.PREFIX + '悠闲哼歌.webm', {'悠闲哼歌': {'id': 'youxian-hengga', 'title': '悠闲哼歌'}}), ('youxian-hengga', '悠闲哼歌'))

    def test_new_source_id_deterministic(self):
        first = sync.stable_id(sync.PREFIX + '新增动作.webm', {})
        self.assertEqual(first, sync.stable_id(sync.PREFIX + '新增动作.webm', {}))
        self.assertRegex(first[0], '^action-[a-f0-9]{16}$')
        self.assertNotEqual(first[0], sync.stable_id(sync.PREFIX + '其他动作.webm', {})[0])

    def test_unsafe_id_rejected(self):
        for value in ['../unsafe', '/etc/passwd', '-x', '中文', 'a/b', '']:
            with self.subTest(value=value), self.assertRaises(RuntimeError):
                sync.stable_id(sync.PREFIX + 'x.webm', {'x': {'id': value}})

    def test_source_hash_checked(self):
        with patch.object(sync, 'download', return_value=b'wrong'):
            with self.assertRaisesRegex(RuntimeError, 'blob hash mismatch'):
                sync.source_bytes('a'*40, {'path': sync.PREFIX + 'x.webm', 'sha': 'b'*40})

    def test_truncated_inventory_rejected(self):
        with patch.object(sync, 'api', side_effect=[{'default_branch': 'main'}, {'sha': 'a'*40}, {'truncated': True, 'tree': []}]):
            with self.assertRaisesRegex(RuntimeError, 'Truncated'):
                sync.pinned_tree()

    def test_license_removed_or_changed_blocks(self):
        policy = {'licenseGuards': {'LICENSE': 'a'*40}}
        for tree in [{}, {'LICENSE': {'type': 'blob', 'mode': '100644', 'sha': 'b'*40}}, {'LICENSE': {'type': 'blob', 'mode': '120000', 'sha': 'a'*40}}]:
            with self.assertRaisesRegex(RuntimeError, 'manual review'):
                sync.check_licenses('c'*40, tree, policy)

    def test_legacy_zip_protected(self):
        with self.assertRaisesRegex(RuntimeError, 'Legacy package protection'):
            sync.validate_legacy({'clip': 'youxian-hengga', 'releaseTag': 'youxian-hengga-v1', 'package': {'sha256': 'bad'}})

    def test_release_retry_never_overwrites(self):
        with tempfile.TemporaryDirectory() as temp:
            package = Path(temp) / 'test.zip'
            package.write_bytes(b'zip')
            with patch.object(sync, 'api', return_value={'assets': [{'name': package.name, 'size': 3, 'digest': 'sha256:wrong'}]}), patch.object(sync.subprocess, 'run') as run:
                with self.assertRaisesRegex(RuntimeError, 'refusing overwrite'):
                    sync.release(package, 'test-tag', 'title', publish=True)
                run.assert_not_called()

    def test_release_same_digest_is_noop(self):
        with tempfile.TemporaryDirectory() as temp:
            package = Path(temp) / 'test.zip'
            package.write_bytes(b'zip')
            item = {'name': package.name, 'size': 3, 'state': 'uploaded', 'digest': 'sha256:' + hashlib.sha256(b'zip').hexdigest()}
            with patch.object(sync, 'api', return_value={'assets': [item]}), patch.object(sync.subprocess, 'run') as run:
                sync.release(package, 'test-tag', 'title', publish=True)
                run.assert_not_called()

    def test_publish_cannot_limit_actions(self):
        with patch('sys.argv', ['sync_upstream.py', '--publish', '--max-actions', '1']):
            with self.assertRaises(SystemExit) as context:
                sync.main()
            self.assertNotEqual(context.exception.code, 0)

if __name__ == '__main__':
    unittest.main()
