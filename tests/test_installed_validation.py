"""Independent installed-revision regressions; all changes are temporary copies."""
import copy
from fractions import Fraction
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import sync_validation as validation
import test_validation as fixtures


class InstalledValidation(unittest.TestCase):
    pack = fixtures.OutputValidation.pack

    def setUp(self):
        fixtures.OutputValidation.setUp(self)
        self.root = self.base / 'repo'
        self.root.mkdir()
        # Historical compatibility guards use an explicitly synthetic legacy
        # fixture. No retired production image is kept just to satisfy tests.
        audit = json.loads((ROOT / 'legacy-v1.json').read_text())
        record = {'schema': 1, 'catalogEntry': copy.deepcopy(audit['catalogEntry']), 'files': {}}
        shutil.copyfile(ROOT / 'LICENSE.txt', self.root / 'LICENSE.txt')
        for name, data in {
                'actions/youxian-hengga/LICENSE.txt': b'Frozen synthetic legacy license',
                'actions/youxian-hengga/manifest.json': b'{}',
                'actions/youxian-hengga/frames/000.webp': fixtures.sample_webp()}.items():
            dest = self.root / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
            record['files'][name] = validation.sha(data)
        (self.root / 'legacy-v1.json').write_text(json.dumps(record))
        revision = '0123456789abcdef'
        self.relative = f'actions/test-action/revisions/{revision}'
        self.installed = self.root / self.relative
        self.installed.parent.mkdir(parents=True)
        shutil.copytree(self.output, self.installed)
        tag = 'test-action-12fps-' + revision
        self.item = {'clip': 'test-action', 'title': 'Test action', 'version': revision,
                     'format': 'static-webp-frames', 'profile': 'webp-q85-alpha-12fps-v2',
                     'recipe': 'c'*64, 'width': 640, 'height': 360, 'frameCount': 1,
                     'durationMs': 83, 'license': 'CC-BY-NC-SA-4.0', 'status': 'active',
                     'licenseSha256': validation.sha((ROOT / 'LICENSE.txt').read_bytes()),
                     'build85Compatible': False, 'manifest': self.relative + '/manifest.json',
                     'frames': self.relative + '/frames/', 'releaseTag': tag,
                     'package': {'url': 'https://github.com/taikulayyds/Dafeiyu-Pet-actions/releases/download/' + tag + '/' + tag + '.zip',
                                 'bytes': self.package.stat().st_size,
                                 'sha256': revision + '0'*48},
                     'source': {**self.manifest['source'], 'gitBlob': 'd'*40}}
        self.catalog = {'schema': 1, 'repository': 'https://github.com/taikulayyds/Dafeiyu-Pet-actions',
                        'actions': [record['catalogEntry'], self.item], 'legacy': [record['catalogEntry']]}

    def validate(self):
        return validation.validate_catalog(self.root, self.catalog)

    def save_manifest(self):
        (self.installed / 'manifest.json').write_text(json.dumps(self.manifest))

    def replace_frame(self, data):
        (self.installed / 'frames/000.webp').write_bytes(data)
        self.manifest['frames'][0].update(bytes=len(data), sha256=validation.sha(data))
        self.save_manifest()

    def test_whole_catalog_legacy_and_installed_revision_pass(self):
        self.validate()
        self.item['status'] = 'missing-upstream'
        self.validate()

    def test_all_catalog_metadata_mutations_rejected(self):
        mutations = {'title': 'different', 'version': 'f'*16, 'format': 'png',
                     'profile': 'unknown', 'recipe': 'short', 'width': 639, 'height': 359,
                     'frameCount': True, 'durationMs': True, 'license': 'MIT',
                     'status': 'deleted', 'licenseSha256': '0'*64,
                     'build85Compatible': True, 'manifest': '../escape', 'frames': 'actions/wrong/',
                     'releaseTag': 'wrong', 'source': {**self.item['source'], 'commit': 'f'*40}}
        original = copy.deepcopy(self.item)
        for key, value in mutations.items():
            with self.subTest(field=key):
                self.item.clear()
                self.item.update(copy.deepcopy(original))
                self.item[key] = value
                with self.assertRaises(RuntimeError):
                    self.validate()

    def test_package_metadata_mutations_rejected(self):
        original = copy.deepcopy(self.item['package'])
        for key, value in [('url', 'https://example.com/file.zip'), ('bytes', True),
                           ('bytes', 0), ('bytes', validation.MAX_BYTES + 1), ('sha256', 'f'*64)]:
            with self.subTest(field=key, value=value):
                self.item['package'] = {**original, key: value}
                with self.assertRaises(RuntimeError):
                    self.validate()

    def test_extra_ordinary_file_rejected(self):
        (self.installed / 'unexpected.txt').write_text('extra')
        with self.assertRaises(RuntimeError):
            self.validate()

    def test_installed_invalid_webp_with_matching_hash_rejected(self):
        self.replace_frame(b'not WebP')
        with self.assertRaises(RuntimeError):
            self.validate()

    def test_installed_license_cannot_be_self_attested(self):
        bad = b'MIT; no original attribution'
        (self.installed / 'LICENSE.txt').write_bytes(bad)
        self.item['licenseSha256'] = validation.sha(bad)
        with self.assertRaises(RuntimeError):
            self.validate()

    def test_legacy_old_license_not_replaced_by_root_template(self):
        retained = self.root / 'actions/youxian-hengga/LICENSE.txt'
        self.assertNotEqual(retained.read_bytes(), (self.root / 'LICENSE.txt').read_bytes())
        self.validate()
        retained.write_bytes((self.root / 'LICENSE.txt').read_bytes())
        with self.assertRaises(RuntimeError):
            self.validate()

    def test_legacy_metadata_cannot_change(self):
        self.catalog['actions'][0]['title'] = 'changed'
        with self.assertRaises(RuntimeError):
            self.validate()

    def test_legacy_parent_symlink_rejected(self):
        retained = self.root / 'actions/youxian-hengga'
        moved = self.root / 'retained'
        retained.rename(moved)
        retained.symlink_to(moved, target_is_directory=True)
        with self.assertRaises(RuntimeError):
            self.validate()

    def test_duplicate_and_dropped_legacy_rejected(self):
        self.catalog['actions'].append(copy.deepcopy(self.item))
        with self.assertRaises(RuntimeError):
            self.validate()
        self.catalog['actions'] = [self.item]
        with self.assertRaises(RuntimeError):
            self.validate()

    def test_all_frames_really_decoded(self):
        second = copy.deepcopy(self.manifest['frames'][0])
        second['file'] = 'frames/001.webp'
        data = b'invalid second frame'
        second.update(bytes=len(data), sha256=validation.sha(data), durationMs=83)
        (self.installed / second['file']).write_bytes(data)
        self.manifest['frames'].append(second)
        self.manifest['frameCount'] = self.item['frameCount'] = 2
        self.item['durationMs'] = 166
        self.save_manifest()
        with self.assertRaises(RuntimeError):
            self.validate()

    def test_installed_nonterminal_one_ms_rejected(self):
        second = copy.deepcopy(self.manifest['frames'][0])
        second['file'] = 'frames/001.webp'
        (self.installed / second['file']).write_bytes((self.installed / 'frames/000.webp').read_bytes())
        self.manifest['frames'].append(second)
        self.manifest['frames'][0]['durationMs'] = 1
        self.manifest['frameCount'] = self.item['frameCount'] = 2
        self.item['durationMs'] = 84
        self.save_manifest()
        with self.assertRaises(RuntimeError):
            self.validate()


class DecodeAndTiming(unittest.TestCase):
    """Exercise the common gate with actual WebP bytes, not mocked decoding."""
    pack = fixtures.OutputValidation.pack

    def setUp(self):
        fixtures.OutputValidation.setUp(self)

    def validate(self):
        return validation.validate_action(self.output,
            expected_license_bytes=(ROOT / 'LICENSE.txt').read_bytes())

    def replace_image(self, image, **kwargs):
        path = self.output / 'frames/000.webp'
        image.save(path, **kwargs)
        data = path.read_bytes()
        self.manifest['frames'][0].update(bytes=len(data), sha256=validation.sha(data))
        (self.output / 'manifest.json').write_text(json.dumps(self.manifest))

    def test_png_renamed_webp_and_wrong_canvas_rejected(self):
        from PIL import Image
        with Image.open(__import__('io').BytesIO(fixtures.sample_webp())) as source:
            image = source.copy()
        self.replace_image(image, format='PNG')
        with self.assertRaises(RuntimeError):
            self.validate()
        self.replace_image(image.resize((639, 360)), format='WEBP')
        with self.assertRaises(RuntimeError):
            self.validate()

    def test_invisible_opaque_and_animated_frames_rejected(self):
        from PIL import Image
        with Image.open(__import__('io').BytesIO(fixtures.sample_webp())) as source:
            image = source.copy().convert('RGBA')
        image.putalpha(0)
        self.replace_image(image, format='WEBP')
        with self.assertRaises(RuntimeError):
            self.validate()
        image.putalpha(255)
        self.replace_image(image, format='WEBP')
        with self.assertRaises(RuntimeError):
            self.validate()
        with Image.open(__import__('io').BytesIO(fixtures.sample_webp())) as source:
            original = source.copy()
        changed = original.copy()
        changed.putpixel((320, 180), (255, 0, 0, 255))
        self.replace_image(original, format='WEBP', save_all=True, append_images=[changed], duration=83)
        with self.assertRaises(RuntimeError):
            self.validate()

    def test_action_wide_alpha_extrema_gate(self):
        # Also prove that a hypothetical decoder retaining an opaque alpha
        # channel cannot bypass the actual pixel-range check.
        with patch.object(validation, 'decode_webp', return_value=(255, 255)):
            with self.assertRaisesRegex(RuntimeError, 'no transparency'):
                self.validate()

    def test_specified_full_timing_classes_with_real_decoded_frames(self):
        data = fixtures.sample_webp()
        for total_ms, count in ((10042, 121), (5125, 62), (4917, 59)):
            with self.subTest(total_ms=total_ms):
                frames = []
                for path in (self.output / 'frames').iterdir():
                    path.unlink()
                for i in range(count):
                    name = f'frames/{i:03d}.webp'
                    (self.output / name).write_bytes(data)
                    duration = (round(Fraction((i + 1) * 1000, 12)) if i < count - 1 else total_ms) - round(Fraction(i * 1000, 12))
                    frames.append({'file': name, 'bytes': len(data), 'sha256': validation.sha(data),
                                   'durationMs': duration})
                self.manifest.update(frameCount=count, frames=frames, durationMs=total_ms)
                (self.output / 'manifest.json').write_text(json.dumps(self.manifest))
                result = validation.validate_action(self.output,
                    expected_license_bytes=(ROOT / 'LICENSE.txt').read_bytes(), expected_duration_ms=total_ms)
                self.assertEqual(result['frameCount'], count)
                self.assertEqual(sum(f['durationMs'] for f in result['frames']), total_ms)

    def test_explicit_trusted_license_override_and_default(self):
        approved = (ROOT / 'LICENSE.txt').read_bytes()
        self.assertEqual(validation.validated_output(self.output, self.package, **self.kwargs,
                         expected_license_bytes=approved), self.manifest)
        with self.assertRaises(RuntimeError):
            validation.validated_output(self.output, self.package, **self.kwargs,
                                        expected_license_bytes=b'not the approved legal text')
