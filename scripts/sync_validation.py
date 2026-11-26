#!/usr/bin/env python3
"""Closed-output validation and legacy file invariants, independent of publishing."""
import hashlib
import json
import re
from fractions import Fraction
import os
from pathlib import Path
import stat
import subprocess
import zipfile

MAX_BYTES = 8 * 1024 * 1024

def sha(data):
    return hashlib.sha256(data).hexdigest()

def require_plain_tree(root):
    if root.is_symlink() or not root.is_dir():
        raise RuntimeError('Unsafe package directory')
    for base, dirs, files in os.walk(root, followlinks=False):
        for name in dirs + files:
            mode = (Path(base) / name).lstat().st_mode
            if not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
                raise RuntimeError('Symlink or special file in package output')

def check_legacy(root):
    record = json.loads((root / 'legacy-v1.json').read_text())
    status = record.get('status', 'retained')
    if status not in ('retained', 'retired'):
        raise RuntimeError('Unknown legacy retirement status')
    if status == 'retired':
        replacement = record.get('replacement', {})
        revision = replacement.get('version')
        if (not isinstance(revision, str) or not re.fullmatch('[0-9a-f]{16}', revision)
                or replacement.get('clip') != 'youxian-hengga'
                or replacement.get('releaseTag') != 'youxian-hengga-12fps-' + revision
                or not isinstance(replacement.get('packageSHA256'), str)
                or not re.fullmatch('[0-9a-f]{64}', replacement['packageSHA256'])
                or replacement['packageSHA256'][:16] != revision
                or not isinstance(replacement.get('manifestSHA256'), str)
                or not re.fullmatch('[0-9a-f]{64}', replacement['manifestSHA256'])):
            raise RuntimeError('Invalid verified legacy replacement')
        manifest = safe_destination(root, 'actions/youxian-hengga/revisions/' + revision + '/manifest.json')
        if not manifest.is_file() or sha(manifest.read_bytes()) != replacement['manifestSHA256']:
            raise RuntimeError('Verified legacy replacement is missing or changed')
    for relative, expected in record['files'].items():
        path = safe_destination(root, relative)
        if status == 'retired':
            if path.exists() or path.is_symlink():
                raise RuntimeError('Retired legacy resource reintroduced: ' + relative)
        elif path.is_symlink() or not path.is_file() or sha(path.read_bytes()) != expected:
            raise RuntimeError('Legacy v1 retained file changed: ' + relative)
    return record['catalogEntry']

def clean_environment():
    # Decoders see no GitHub, provider, cloud, cookie or proxy credentials.
    keep = ('PATH', 'HOME', 'LANG', 'LC_ALL', 'TMPDIR', 'SYSTEMROOT')
    return {key: os.environ[key] for key in keep if key in os.environ}

def recipe(root):
    parts = [(root / 'scripts' / 'convert_action.py').read_bytes(), (root / 'LICENSE.txt').read_bytes()]
    for command in ('ffmpeg', 'ffprobe', 'cwebp'):
        out = subprocess.run([command, '-version'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True, timeout=30, env=clean_environment())
        parts.append(out.stdout + out.stderr)
    return sha(b'\0'.join(parts))

def sampling_count(duration_ms):
    """12fps count with <=0.5ms container rounding tolerance near a tick."""
    duration = Fraction(duration_ms, 1000)
    ticks = duration * 12
    nearest = round(ticks)
    if nearest > 0 and abs(duration - Fraction(nearest, 12)) <= Fraction(1, 2000):
        return nearest
    return (ticks.numerator + ticks.denominator - 1) // ticks.denominator


def validate_action(directory, *, expected_license_bytes, expected_duration_ms=None):
    """Shared installed/output schema, decode, timing and attribution gate.

    expected_duration_ms is independently probed by the orchestrator, never taken
    from converter stdout. Installed resources have no source video available.
    """
    directory = Path(directory)
    require_plain_tree(directory)
    manifest = json.loads((directory / 'manifest.json').read_text('utf-8'))
    required = {'schema', 'clip', 'title', 'width', 'height', 'frameCount', 'source', 'license', 'frames'}
    if not isinstance(manifest, dict) or not required.issubset(manifest) or set(manifest) - required - {'durationMs'}:
        raise RuntimeError('Invalid manifest fields')
    if type(manifest['schema']) is not int or manifest['schema'] != 1 or not isinstance(manifest['clip'], str) or not re.fullmatch('[a-z0-9]+(?:-[a-z0-9]+)*', manifest['clip']):
        raise RuntimeError('Manifest identity/schema mismatch')
    if not isinstance(manifest['title'], str) or not manifest['title'].strip() or len(manifest['title']) > 512:
        raise RuntimeError('Invalid manifest title')
    if any(type(manifest[k]) is not int for k in ('width', 'height')) or (manifest['width'], manifest['height']) != (640, 360) or manifest['license'] != 'CC-BY-NC-SA-4.0':
        raise RuntimeError('Manifest canvas/license mismatch')
    source = manifest['source']
    if not isinstance(source, dict) or set(source) != {'repository', 'commit', 'file', 'sha256'} or source['repository'] != 'https://github.com/PC2005-cloud/dsh-pet':
        raise RuntimeError('Invalid manifest source')
    if not isinstance(source['commit'], str) or not re.fullmatch('[0-9a-f]{40}', source['commit']) or not isinstance(source['sha256'], str) or not re.fullmatch('[0-9a-f]{64}', source['sha256']):
        raise RuntimeError('Invalid manifest source hashes')
    if not isinstance(source['file'], str) or not re.fullmatch(r'dsh-pet/assets/webm/[^/\\\x00]+\.webm', source['file']):
        raise RuntimeError('Invalid manifest source file')
    frames = manifest['frames']
    if type(manifest['frameCount']) is not int or not 1 <= manifest['frameCount'] <= 720 or not isinstance(frames, list) or len(frames) != manifest['frameCount']:
        raise RuntimeError('Invalid manifest frame count')
    expected = {'manifest.json', 'LICENSE.txt'}
    duration = 0
    alpha_min = 255
    for index, frame in enumerate(frames):
        name = f'frames/{index:03d}.webp'
        if not isinstance(frame, dict) or set(frame) != {'file', 'bytes', 'sha256', 'durationMs'} or frame['file'] != name or type(frame['durationMs']) is not int or not 1 <= frame['durationMs'] <= 100:
            raise RuntimeError('Invalid frame timing or path')
        if type(frame['bytes']) is not int or not 0 < frame['bytes'] <= 256 * 1024 or not isinstance(frame['sha256'], str) or not re.fullmatch('[0-9a-f]{64}', frame['sha256']):
            raise RuntimeError('Invalid frame hash/size metadata')
        data = (directory / name).read_bytes()
        if len(data) != frame['bytes'] or sha(data) != frame['sha256']:
            raise RuntimeError('Frame hash/size mismatch')
        boundary_delta = round(Fraction((index + 1) * 1000, 12)) - round(Fraction(index * 1000, 12))
        if index < len(frames) - 1 and frame['durationMs'] != boundary_delta:
            raise RuntimeError('Frame timing does not match the 12fps sampling grid')
        # The final segment may exceed its rounded tick by 1ms, but only if
        # the action total still snaps to this frame count below.
        if index == len(frames) - 1 and frame['durationMs'] > boundary_delta + 1:
            raise RuntimeError('Final frame exceeds container rounding tolerance')
        low, _ = decode_webp(directory / name)
        alpha_min = min(alpha_min, low)
        expected.add(name)
        duration += frame['durationMs']
    if not 1 <= duration <= 60000 or sampling_count(duration) != len(frames):
        raise RuntimeError('Action duration/frame count differs from 12fps sampling')
    if expected_duration_ms is not None and (type(expected_duration_ms) is not int or duration != expected_duration_ms):
        raise RuntimeError('Package timing differs from the actual source duration')
    if 'durationMs' in manifest and (type(manifest['durationMs']) is not int or manifest['durationMs'] != duration):
        raise RuntimeError('Action total duration mismatch')
    if alpha_min == 255:
        raise RuntimeError('Action has no transparency')
    actual = {path.relative_to(directory).as_posix() for path in directory.rglob('*') if path.is_file()}
    if actual != expected:
        raise RuntimeError('Unexpected files in action output')
    if not isinstance(expected_license_bytes, bytes) or not expected_license_bytes or (directory / 'LICENSE.txt').read_bytes() != expected_license_bytes:
        raise RuntimeError('Package license differs from approved attribution/legal text')
    if sum((directory / name).stat().st_size for name in expected) > MAX_BYTES:
        raise RuntimeError('Action expansion size limit exceeded')
    return manifest


def validated_output(output, package, *, clip, title, commit, source_file, source_sha,
                     expected_duration_ms=None, expected_license_bytes=None):
    output, package = Path(output), Path(package)
    if package.is_symlink() or not package.is_file() or package.resolve().parent != output.parent.resolve():
        raise RuntimeError('Converter returned unsafe ZIP path')
    if not 0 < package.stat().st_size <= MAX_BYTES:
        raise RuntimeError('ZIP size limit exceeded')
    if expected_license_bytes is None:
        expected_license_bytes = (Path(__file__).resolve().parents[1] / 'LICENSE.txt').read_bytes()
    manifest = validate_action(output, expected_license_bytes=expected_license_bytes,
                               expected_duration_ms=expected_duration_ms)
    if manifest['clip'] != clip or manifest['title'] != title:
        raise RuntimeError('Manifest identity mismatch')
    expected_source = {'repository': 'https://github.com/PC2005-cloud/dsh-pet', 'commit': commit, 'file': source_file, 'sha256': source_sha}
    if manifest['source'] != expected_source:
        raise RuntimeError('Manifest pinned source mismatch')
    expected = {'manifest.json', 'LICENSE.txt'} | {frame['file'] for frame in manifest['frames']}
    with zipfile.ZipFile(package) as archive:
        infos = archive.infolist()
        if len(infos) != len(expected) or {x.filename for x in infos} != expected:
            raise RuntimeError('ZIP member set mismatch')
        total = 0
        for info in infos:
            mode = info.external_attr >> 16
            if info.compress_type not in (0, 8) or info.flag_bits & (1 | 8) or info.is_dir() or stat.S_IFMT(mode) not in (0, stat.S_IFREG):
                raise RuntimeError('Unsupported ZIP entry')
            total += info.file_size
            if total > MAX_BYTES or archive.read(info) != (output / info.filename).read_bytes():
                raise RuntimeError('ZIP expansion/content mismatch')
    return manifest

def safe_destination(root, relative):
    path = root / relative
    for component in [path, *path.parents]:
        if component == root:
            break
        if component.is_symlink():
            raise RuntimeError('Symlink in destination ancestry')
    if root.resolve() not in path.resolve().parents:
        raise RuntimeError('Destination escapes repository')
    return path

def validate_catalog(root, catalog):
    root = Path(root)
    legacy = check_legacy(root)
    if not isinstance(catalog, dict) or type(catalog.get('schema')) is not int or catalog['schema'] != 1 or catalog.get('repository') != 'https://github.com/taikulayyds/Dafeiyu-Pet-actions' or not isinstance(catalog.get('actions'), list):
        raise RuntimeError('Invalid catalog schema/repository')
    retired = json.loads((root / 'legacy-v1.json').read_text()).get('status') == 'retired'
    if retired and catalog.get('legacy') != []:
        raise RuntimeError('Retired legacy entry must not be advertised')
    if not retired and 'legacy' in catalog and catalog['legacy'] != [legacy]:
        raise RuntimeError('Legacy catalog retention changed')
    approved_license = (root / 'LICENSE.txt').read_bytes()
    seen = set()
    for item in catalog['actions']:
        if not isinstance(item, dict) or not isinstance(item.get('clip'), str) or not re.fullmatch('[a-z0-9]+(?:-[a-z0-9]+)*', item['clip']):
            raise RuntimeError('Unsafe catalog clip')
        clip = item['clip']
        if clip in seen:
            raise RuntimeError('Duplicate catalog action')
        seen.add(clip)
        # Legacy v1 intentionally uses its frozen original timing and LICENSE,
        # not the new 12fps grid or the current root attribution template.
        if item.get('manifest') == legacy['manifest']:
            if retired:
                raise RuntimeError('Retired legacy clip cannot become active again')
            for key, value in legacy.items():
                actual = item.get(key)
                if key == 'source' and isinstance(actual, dict):
                    actual = {k: v for k, v in actual.items() if k != 'gitBlob'}
                if actual != value:
                    raise RuntimeError('Legacy catalog metadata changed: ' + key)
            if set(item) - set(legacy) - {'status', 'build85Compatible'}:
                raise RuntimeError('Unexpected legacy catalog fields')
            if 'build85Compatible' in item and item['build85Compatible'] is not True:
                raise RuntimeError('Legacy compatibility changed')
            if 'status' in item and item['status'] not in ('active', 'missing-upstream'):
                raise RuntimeError('Invalid legacy status')
            if 'gitBlob' in item['source'] and not re.fullmatch('[a-f0-9]{40}', str(item['source']['gitBlob'])):
                raise RuntimeError('Invalid legacy source blob')
            continue
        required = {'clip', 'title', 'version', 'format', 'profile', 'recipe', 'width', 'height',
                    'frameCount', 'durationMs', 'license', 'status', 'licenseSha256',
                    'build85Compatible', 'manifest', 'frames', 'releaseTag', 'package', 'source'}
        if set(item) != required:
            raise RuntimeError('Invalid installed catalog metadata fields')
        revision = item['version']
        if not isinstance(revision, str) or not re.fullmatch('[a-f0-9]{16}', revision):
            raise RuntimeError('Invalid installed revision')
        relative = f'actions/{clip}/revisions/{revision}'
        if item['manifest'] != relative + '/manifest.json' or item['frames'] != relative + '/frames/':
            raise RuntimeError('Unsafe catalog resource path')
        directory = safe_destination(root, relative)
        manifest = validate_action(directory, expected_license_bytes=approved_license,
                                   expected_duration_ms=item['durationMs'])
        for key in ('clip', 'title', 'width', 'height', 'frameCount', 'license'):
            if type(item[key]) is not type(manifest[key]) or item[key] != manifest[key]:
                raise RuntimeError('Catalog/manifest metadata mismatch: ' + key)
        source = item['source']
        if not isinstance(source, dict) or set(source) != set(manifest['source']) | {'gitBlob'} or {k: v for k, v in source.items() if k != 'gitBlob'} != manifest['source'] or not isinstance(source['gitBlob'], str) or not re.fullmatch('[0-9a-f]{40}', source['gitBlob']):
            raise RuntimeError('Catalog/manifest source mismatch')
        if item['format'] != 'static-webp-frames' or item['profile'] != 'webp-q85-alpha-12fps-v2' or item['status'] not in ('active', 'missing-upstream') or item['build85Compatible'] is not False:
            raise RuntimeError('Invalid installed action profile/status/compatibility')
        if not isinstance(item['recipe'], str) or not re.fullmatch('[0-9a-f]{64}', item['recipe']) or item['licenseSha256'] != sha(approved_license):
            raise RuntimeError('Invalid installed recipe/license digest')
        tag = clip + '-12fps-' + revision
        package = item['package']
        if item['releaseTag'] != tag or not isinstance(package, dict) or set(package) != {'url', 'bytes', 'sha256'}:
            raise RuntimeError('Invalid installed package metadata')
        if type(package['bytes']) is not int or not 0 < package['bytes'] <= MAX_BYTES or not isinstance(package['sha256'], str) or not re.fullmatch('[0-9a-f]{64}', package['sha256']) or package['sha256'][:16] != revision:
            raise RuntimeError('Installed package size/digest/revision mismatch')
        if package['url'] != f'https://github.com/taikulayyds/Dafeiyu-Pet-actions/releases/download/{tag}/{tag}.zip':
            raise RuntimeError('Invalid installed package URL')
    if legacy['clip'] not in seen:
        raise RuntimeError('Legacy catalog action was dropped')

def decode_webp(path):
    # Pillow/libwebp is independent of the converter's ffmpeg/cwebp process.
    from PIL import Image
    try:
        with Image.open(path) as image:
            if image.format != 'WEBP' or image.size != (640, 360) or getattr(image, 'n_frames', 1) != 1 or 'A' not in image.getbands():
                raise RuntimeError('Frame is not a static transparent 640x360 WebP')
            image.load()
            low, high = image.getchannel('A').getextrema()
            if high == 0:
                raise RuntimeError('Frame is entirely transparent')
            return low, high
    except RuntimeError:
        raise
    except Exception as error:
        raise RuntimeError('Independent WebP decode failed') from error
