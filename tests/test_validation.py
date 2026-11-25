"""Offline safety regressions. Temporary fixtures only; never mutate real assets.

Run: PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
"""
import copy
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import sync_validation as validation


class OutputValidation(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='dfy-validation-test-')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.output = self.base / 'action'
        (self.output / 'frames').mkdir(parents=True)
        # Read-only retained WebP copy; validation decodes it independently.
        data = (ROOT / 'actions/youxian-hengga/frames/000.webp').read_bytes()
        (self.output / 'frames/000.webp').write_bytes(data)
        (self.output / 'LICENSE.txt').write_bytes((ROOT / 'LICENSE.txt').read_bytes())
        self.kwargs = dict(clip='test-action', title='Test action', commit='a'*40,
                           source_file='dsh-pet/assets/webm/test.webm', source_sha='b'*64)
        self.manifest = {
            'schema': 1, 'clip': self.kwargs['clip'], 'title': self.kwargs['title'],
            'width': 640, 'height': 360, 'frameCount': 1,
            'source': {'repository': 'https://github.com/PC2005-cloud/dsh-pet',
                       'commit': self.kwargs['commit'], 'file': self.kwargs['source_file'],
                       'sha256': self.kwargs['source_sha']},
            'license': 'CC-BY-NC-SA-4.0',
            'frames': [{'file': 'frames/000.webp', 'bytes': len(data),
                        'sha256': validation.sha(data), 'durationMs': 83}]}
        self.package = self.base / 'action.zip'
        self.pack()

    def pack(self, extra=None, duplicate=False, compression=zipfile.ZIP_DEFLATED):
        (self.output / 'manifest.json').write_text(json.dumps(self.manifest), encoding='utf-8')
        with zipfile.ZipFile(self.package, 'w', compression=compression) as archive:
            for path in sorted(self.output.rglob('*')):
                if path.is_file():
                    archive.write(path, path.relative_to(self.output).as_posix())
            if extra:
                archive.writestr(extra, b'not allowed')
            if duplicate:
                archive.writestr('LICENSE.txt', b'duplicate')

    def validate(self):
        return validation.validated_output(self.output, self.package, **self.kwargs)

    def test_sampling_count_matches_converter_rounding_edges(self):
        self.assertEqual([validation.sampling_count(ms) for ms in (10042, 5125, 4917)], [121, 62, 59])

    def test_expected_source_duration_is_independent_gate(self):
        with self.assertRaises(RuntimeError):
            validation.validated_output(self.output, self.package, **self.kwargs, expected_duration_ms=84)
        self.assertEqual(validation.validated_output(self.output, self.package, **self.kwargs,
                                                     expected_duration_ms=83), self.manifest)

    def test_final_frame_allows_only_source_rounding_tolerance(self):
        second = copy.deepcopy(self.manifest['frames'][0])
        second['file'] = 'frames/001.webp'
        (self.output / second['file']).write_bytes((self.output / 'frames/000.webp').read_bytes())
        self.manifest['frames'].append(second)
        self.manifest['frameCount'] = 2
        self.manifest['frames'][0]['durationMs'] = 83
        second['durationMs'] = 84
        self.pack()
        self.assertEqual(self.validate()['frameCount'], 2)
        second['durationMs'] = 85
        self.pack()
        with self.assertRaises(RuntimeError):
            self.validate()

    def test_valid_output(self):
        self.assertEqual(self.validate(), self.manifest)

    def test_identity_schema_canvas_license_source_rejected(self):
        mutations = [('schema', 2), ('clip', '../escape'), ('title', 'wrong'),
                     ('width', 1), ('height', 1), ('license', 'MIT'),
                     ('source', {**self.manifest['source'], 'sha256': 'c'*64}),
                     ('source', {**self.manifest['source'], 'commit': 'c'*40}),
                     ('source', {**self.manifest['source'], 'file': '../wrong'})]
        original = copy.deepcopy(self.manifest)
        for key, value in mutations:
            with self.subTest(field=key, value=value):
                self.manifest = copy.deepcopy(original)
                self.manifest[key] = value
                self.pack()
                with self.assertRaises(RuntimeError):
                    self.validate()

    def test_extra_manifest_field_rejected(self):
        self.manifest['unexpected'] = 'value'
        self.pack()
        with self.assertRaises(RuntimeError):
            self.validate()

    def test_frame_count_types_and_bounds_rejected(self):
        for value in (True, 0, -1, 721, 1.0, '1', 2):
            with self.subTest(value=value):
                self.manifest['frameCount'] = value
                self.pack()
                with self.assertRaises(RuntimeError):
                    self.validate()

    def test_bad_frame_paths_rejected(self):
        for value in ('../outside.webp', '/etc/passwd', 'frames/001.webp', 'frames/000.webp/..'):
            with self.subTest(value=value):
                self.manifest['frames'][0]['file'] = value
                self.pack()
                with self.assertRaises(RuntimeError):
                    self.validate()

    def test_bad_frame_timing_rejected(self):
        for value in (True, 0, -1, 101, 83.0, '83'):
            with self.subTest(value=value):
                self.manifest['frames'][0]['durationMs'] = value
                self.pack()
                with self.assertRaises(RuntimeError):
                    self.validate()

    def test_wrong_frame_hash_and_size_rejected(self):
        for key, value in (('sha256', '0'*64), ('bytes', 0)):
            with self.subTest(field=key):
                original = self.manifest['frames'][0][key]
                self.manifest['frames'][0][key] = value
                self.pack()
                with self.assertRaises(RuntimeError):
                    self.validate()
                self.manifest['frames'][0][key] = original

    def test_extra_output_file_rejected(self):
        (self.output / 'unexpected.py').write_text('not executable')
        self.pack()
        with self.assertRaises(RuntimeError):
            self.validate()

    def test_empty_license_rejected(self):
        (self.output / 'LICENSE.txt').write_bytes(b'')
        self.pack()
        with self.assertRaises(RuntimeError):
            self.validate()

    def test_external_zip_path_rejected(self):
        other = self.base / 'other'
        other.mkdir()
        moved = other / 'action.zip'
        moved.write_bytes(self.package.read_bytes())
        self.package = moved
        with self.assertRaises(RuntimeError):
            self.validate()

    def test_zip_symlink_rejected(self):
        original = self.package
        self.package = self.base / 'link.zip'
        self.package.symlink_to(original)
        with self.assertRaises(RuntimeError):
            self.validate()

    def test_output_frame_symlink_rejected(self):
        frame = self.output / 'frames/000.webp'
        target = self.base / 'outside.webp'
        frame.rename(target)
        frame.symlink_to(target)
        with self.assertRaises(RuntimeError):
            self.validate()

    def test_zip_extra_or_traversal_member_rejected(self):
        for name in ('../escape', '/absolute', 'unexpected'):
            with self.subTest(name=name):
                self.pack(extra=name)
                with self.assertRaises(RuntimeError):
                    self.validate()

    def test_zip_duplicate_member_rejected(self):
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter('ignore', UserWarning)
            self.pack(duplicate=True)
        with self.assertRaises(RuntimeError):
            self.validate()

    def test_zip_content_disagrees_with_output_rejected(self):
        (self.output / 'LICENSE.txt').write_text('changed after packaging')
        with self.assertRaises(RuntimeError):
            self.validate()

    def test_unsupported_zip_compression_rejected(self):
        self.pack(compression=zipfile.ZIP_BZIP2)
        with self.assertRaises(RuntimeError):
            self.validate()

    def test_review_gap_non_webp_bytes_must_be_rejected(self):
        # Matching hashes cannot turn arbitrary bytes into a valid image.
        data = b'not a WebP image'
        (self.output / 'frames/000.webp').write_bytes(data)
        self.manifest['frames'][0].update(bytes=len(data), sha256=validation.sha(data))
        self.pack()
        with self.assertRaises(RuntimeError):
            self.validate()

    def test_review_gap_nonterminal_one_ms_frame_is_not_12fps(self):
        # Test a non-final frame, avoiding ambiguity about a short final tail.
        second = copy.deepcopy(self.manifest['frames'][0])
        second['file'] = 'frames/001.webp'
        (self.output / second['file']).write_bytes((self.output / 'frames/000.webp').read_bytes())
        self.manifest['frames'].append(second)
        self.manifest['frameCount'] = 2
        self.manifest['frames'][0]['durationMs'] = 1
        self.pack()
        with self.assertRaises(RuntimeError):
            self.validate()

    def test_review_gap_foreign_license_text_must_be_rejected(self):
        # Merely nonempty text does not preserve the required attribution.
        (self.output / 'LICENSE.txt').write_text('MIT; no original attribution')
        self.pack()
        with self.assertRaises(RuntimeError):
            self.validate()


class RetainedAssetsAndGuards(unittest.TestCase):
    def test_real_legacy_files_unchanged(self):
        record = json.loads((ROOT / 'legacy-v1.json').read_text())
        self.assertEqual(len(record['files']), 123)
        self.assertEqual(validation.check_legacy(ROOT), record['catalogEntry'])
        self.assertEqual(record['catalogEntry']['package']['sha256'],
                         '1719e2260ab889844c9f6ac271c133b1d11ed91087002627960d74146e08d256')

    def test_legacy_mutation_deletion_symlink_rejected_in_fixture(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            retained = root / 'retained.txt'
            record = {'files': {'retained.txt': validation.sha(b'original')},
                      'catalogEntry': {'clip': 'legacy'}}
            (root / 'legacy-v1.json').write_text(json.dumps(record))
            retained.write_bytes(b'original')
            self.assertEqual(validation.check_legacy(root), {'clip': 'legacy'})
            retained.write_bytes(b'changed')
            with self.assertRaises(RuntimeError):
                validation.check_legacy(root)
            retained.unlink()
            with self.assertRaises(RuntimeError):
                validation.check_legacy(root)
            (root / 'target').write_bytes(b'original')
            retained.symlink_to(root / 'target')
            with self.assertRaises(RuntimeError):
                validation.check_legacy(root)

    def test_clean_environment_is_allowlist(self):
        # All values synthetic; diagnostics can never print actual credentials.
        fake = {'PATH': '/usr/bin', 'HOME': '/synthetic-home', 'LANG': 'C',
                'LC_ALL': 'C', 'TMPDIR': '/tmp', 'SYSTEMROOT': '/synthetic',
                'GH_TOKEN': 'fake', 'GITHUB_TOKEN': 'fake', 'OPENAI_API_KEY': 'fake',
                'AWS_SECRET_ACCESS_KEY': 'fake', 'HTTP_PROXY': 'fake',
                'HTTPS_PROXY': 'fake', 'LD_PRELOAD': 'fake', 'PYTHONPATH': 'fake'}
        with patch.dict(os.environ, fake, clear=True):
            cleaned = validation.clean_environment()
        allowed = {'PATH', 'HOME', 'LANG', 'LC_ALL', 'TMPDIR', 'SYSTEMROOT'}
        self.assertEqual(set(cleaned), allowed)
        self.assertEqual(cleaned, {key: fake[key] for key in allowed})

    def test_clean_environment_empty(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(validation.clean_environment(), {})

    def test_safe_destination_rejects_escape_and_symlink_parent(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'root'
            root.mkdir()
            outside = Path(temp) / 'outside'
            outside.mkdir()
            (root / 'actions').symlink_to(outside, target_is_directory=True)
            for relative in ('../escape', str(outside / 'absolute'), 'actions/a/file'):
                with self.subTest(relative=relative), self.assertRaises(RuntimeError):
                    validation.safe_destination(root, relative)

    def test_safe_destination_accepts_new_revision(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            relative = 'actions/test/revisions/' + 'a'*16
            self.assertEqual(validation.safe_destination(root, relative), root / relative)

    def test_workflow_main_and_repository_guards(self):
        workflow = (ROOT / '.github/workflows/sync-upstream.yml').read_text()
        self.assertIn("if: github.repository == 'taikulayyds/Dafeiyu-Pet-actions' && github.ref == 'refs/heads/main'", workflow)
        self.assertRegex(workflow, r'(?m)^\s+ref: main$')
        self.assertIn('persist-credentials: false', workflow)
        self.assertIn('python3 scripts/check_sync_diff.py "$SYNC_BASE_SHA"', workflow)
        self.assertIn('test "$(git rev-parse HEAD^)" = "$SYNC_BASE_SHA"', workflow)
        self.assertIn('git push origin HEAD:main', workflow)
        self.assertNotIn('--force', workflow)
        self.assertNotIn('pull_request_target:', workflow)
        self.assertLess(workflow.index('check_sync_diff.py'), workflow.index("git commit -m"))

    def test_converter_run_uses_clean_environment_and_timeout(self):
        import ast
        source = (ROOT / 'scripts/sync_upstream.py').read_text()
        tree = ast.parse(source)
        calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
                 and isinstance(node.func, ast.Attribute) and node.func.attr == 'run'
                 and node.args and isinstance(node.args[0], ast.Name)
                 and node.args[0].id == 'cmd']
        self.assertEqual(len(calls), 1)
        keywords = {item.arg: item.value for item in calls[0].keywords}
        self.assertEqual(ast.unparse(keywords['env']), 'validation.clean_environment()')
        self.assertGreater(ast.literal_eval(keywords['timeout']), 0)
        self.assertTrue(ast.literal_eval(keywords['check']))

    def test_retained_catalog_actual_validation(self):
        catalog = json.loads((ROOT / 'catalog.json').read_text())
        validation.validate_catalog(ROOT, catalog)


class CommitAllowlist(unittest.TestCase):
    def run_guard(self, raw, head=None, baseline=b''):
        import runpy
        base = 'a'*40
        with patch('sys.argv', ['check_sync_diff.py', base]), \
             patch('subprocess.check_output', side_effect=[(head or base)+'\n', raw, baseline]), \
             patch.object(validation, 'check_legacy', return_value={}), \
             patch('builtins.print'):
            runpy.run_path(str(ROOT / 'scripts/check_sync_diff.py'), run_name='__main__')

    def test_new_revision_and_catalog_allowed(self):
        self.run_guard(b'A\0actions/test/revisions/0123456789abcdef/frames/000.webp\0M\0catalog.json\0')

    def test_existing_revision_new_file_rejected(self):
        prefix = 'actions/test/revisions/0123456789abcdef/'
        baseline = (prefix + 'frames/000.webp\0').encode()
        for name in ('frames/001.webp', 'manifest.json', 'LICENSE.txt', 'unexpected.txt'):
            with self.subTest(name=name), self.assertRaises(SystemExit):
                self.run_guard(('A\0' + prefix + name + '\0').encode(), baseline=baseline)

    def test_other_existing_revision_does_not_block_new_revision(self):
        self.run_guard(b'A\0actions/test/revisions/0123456789abcdef/frames/000.webp\0',
                       baseline=b'actions/test/revisions/fedcba9876543210/manifest.json\0')

    def test_malformed_or_rename_diff_rejected(self):
        for raw in (b'A\0unterminated', b'R100\0catalog.json\0upstream-state.json\0'):
            with self.subTest(raw=raw), self.assertRaises(SystemExit):
                self.run_guard(raw)

    def test_historical_modifications_and_deletions_rejected(self):
        for status in ('M', 'D'):
            for path in ('actions/test/revisions/0123456789abcdef/frames/000.webp',
                         'actions/youxian-hengga/frames/000.webp'):
                with self.subTest(status=status, path=path), self.assertRaises(SystemExit):
                    self.run_guard((status+'\0'+path+'\0').encode())

    def test_unrelated_and_unsafe_paths_rejected(self):
        for path in ('scripts/sync_upstream.py', '.github/workflows/sync-upstream.yml',
                     'main.py', 'actions/../escape', 'actions/test/frames/000.webp'):
            with self.subTest(path=path), self.assertRaises(SystemExit):
                self.run_guard(('A\0'+path+'\0').encode())

    def test_changed_baseline_rejected(self):
        with self.assertRaises(SystemExit):
            self.run_guard(b'', head='b'*40)


if __name__ == '__main__':
    unittest.main()
