#!/usr/bin/env python3
"""Pinned, incremental asset sync. Never executes upstream code."""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from fractions import Fraction

SOURCE = 'PC2005-cloud/dsh-pet'
SOURCE_URL = 'https://github.com/' + SOURCE
DEST = 'taikulayyds/Dafeiyu-Pet-actions'
PROFILE = 'webp-q85-alpha-12fps-v2'
PREFIX = 'dsh-pet/assets/webm/'
ROOT = Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(ROOT / 'scripts'))
import sync_validation as validation

def read_json(path):
    return json.loads(path.read_text(encoding='utf-8'))

def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.' + path.name + '-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)

def download(url, limit, *, api=False):
    headers = {'User-Agent': 'Dafeiyu-Pet-actions-sync', 'Accept': 'application/vnd.github+json' if api else '*/*'}
    token = os.environ.get('GH_TOKEN') or os.environ.get('GITHUB_TOKEN')
    if api and token:
        headers['Authorization'] = 'Bearer ' + token
    for attempt in range(4):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=90) as response:
                value = response.read(limit + 1)
            if len(value) > limit:
                raise RuntimeError('Remote response exceeds the configured size limit')
            return value
        except (urllib.error.URLError, TimeoutError):
            if attempt == 3:
                raise
            time.sleep(2 ** attempt)

def api(path):
    return json.loads(download('https://api.github.com/' + path, 16 * 1024 * 1024, api=True))

def git_blob(data):
    return hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()

def source_bytes(commit, entry, limit=64*1024*1024):
    path = entry['path']
    data = download(f'https://raw.githubusercontent.com/{SOURCE}/{commit}/' + urllib.parse.quote(path), limit)
    if git_blob(data) != entry['sha']:
        raise RuntimeError('Pinned upstream Git blob hash mismatch: ' + path)
    return data

def pinned_tree():
    repo = api('repos/' + SOURCE)
    branch = urllib.parse.quote(repo['default_branch'], safe='')
    commit = api(f'repos/{SOURCE}/commits/{branch}')['sha']
    if not re.fullmatch('[0-9a-f]{40}', commit):
        raise RuntimeError('Invalid upstream commit')
    tree = api(f'repos/{SOURCE}/git/trees/{commit}?recursive=1')
    if tree.get('truncated'):
        raise RuntimeError('Truncated upstream tree; refusing incomplete sync')
    return commit, {x['path']: x for x in tree['tree']}

def check_licenses(commit, tree, policy):
    guards = policy['licenseGuards']
    if not guards:
        raise RuntimeError('License guard policy is empty')
    if 'knownLicensePaths' in policy:
        actual = sorted(path for path in tree if re.fullmatch(r'(?i)(?:license|licence|notice|copying|copyright)(?:[._-].*)?', PurePosixPath(path).name))
        if actual != policy['knownLicensePaths']:
            raise RuntimeError('New or removed upstream license/NOTICE file; manual review required')
    for path, expected in guards.items():
        entry = tree.get(path)
        if not entry or entry.get('type') != 'blob' or entry.get('mode') != '100644' or entry['sha'] != expected:
            raise RuntimeError('Upstream license/attribution changed; manual review required: ' + path)
        source_bytes(commit, entry, 2 * 1024 * 1024)
    for section in policy.get('sectionGuards', []):
        entry = tree.get(section['path'])
        if not entry or entry.get('mode') != '100644':
            raise RuntimeError('Missing attribution file; manual review required')
        text = source_bytes(commit, entry, 2 * 1024 * 1024).decode('utf-8').replace('\r\n', '\n')
        lines = text.splitlines()
        if 'start_heading' in section:
            matches = [i for i, line in enumerate(lines) if line == section['start_heading']]
            if len(matches) != 1:
                raise RuntimeError('Missing or duplicate license heading; manual review required')
            start = matches[0]
            end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith('## ')), len(lines))
            canonical = '\n'.join(lines[start:end]).rstrip('\n') + '\n'
        else:
            matches = [line for line in lines if line.startswith(section['start_line_prefix'])]
            if len(matches) != 1:
                raise RuntimeError('Missing or duplicate attribution line; manual review required')
            canonical = matches[0] + '\n'
        if hashlib.sha256(canonical.encode()).hexdigest() != section['sha256']:
            raise RuntimeError('Upstream attribution section changed; manual review required')

def stable_id(path, mappings):
    name = PurePosixPath(path).stem
    known = mappings.get(name)
    if known:
        ident, title = known['id'], known.get('title', name)
    else:
        # Source filename hash is stable and avoids transliteration collisions.
        ident, title = 'action-' + hashlib.sha256(name.encode()).hexdigest()[:16], name
    if not re.fullmatch('[a-z0-9][a-z0-9-]{0,95}', ident):
        raise RuntimeError('Unsafe action ID: ' + ident)
    return ident, title

def release(package, tag, title, *, publish):
    if not publish:
        return
    env = os.environ.copy()
    env['GH_TOKEN'] = env.get('GH_TOKEN') or env.get('GITHUB_TOKEN', '')
    try:
        existing = api(f'repos/{DEST}/releases/tags/' + urllib.parse.quote(tag, safe=''))
    except urllib.error.HTTPError as error:
        if error.code != 404:
            raise
        existing = None
    if existing is None:
        subprocess.run(['gh', 'release', 'create', tag, str(package), '--repo', DEST,
                        '--title', title, '--notes', '12fps Q85 transparent WebP frames. CC BY-NC-SA 4.0; see manifest and LICENSE inside the ZIP. Not compatible with the single-action Build85 importer unless explicitly marked.',
                        '--latest=false'], env=env, check=True)
    else:
        assets = existing['assets']
        match = next((x for x in assets if x['name'] == package.name), None)
        digest = 'sha256:' + hashlib.sha256(package.read_bytes()).hexdigest()
        if match:
            if match.get('digest') != digest or match.get('size') != package.stat().st_size:
                raise RuntimeError('Immutable release asset mismatch; refusing overwrite: ' + tag)
        else:
            subprocess.run(['gh', 'release', 'upload', tag, str(package), '--repo', DEST], env=env, check=True)
    confirmed = api(f'repos/{DEST}/releases/tags/' + urllib.parse.quote(tag, safe=''))
    asset = next((value for value in confirmed['assets'] if value['name'] == package.name), None)
    if not asset or asset.get('state') != 'uploaded' or asset.get('size') != package.stat().st_size or asset.get('digest') != 'sha256:' + hashlib.sha256(package.read_bytes()).hexdigest():
        raise RuntimeError('Published release asset failed independent digest/size verification')

def independent_source_duration(source):
    """Probe trusted local bytes directly, never trust converter's timing JSON."""
    process = subprocess.run(['ffprobe', '-protocol_whitelist', 'file,pipe', '-v', 'error',
                              '-select_streams', 'v:0', '-show_streams', '-show_format',
                              '-of', 'json', str(source)], check=True, capture_output=True,
                             text=True, timeout=120, env=validation.clean_environment())
    data = json.loads(process.stdout)
    streams = data.get('streams', [])
    if len(streams) != 1:
        raise RuntimeError('Expected exactly one selected video stream')
    stream = streams[0]
    if (stream.get('width'), stream.get('height'), stream.get('codec_name')) != (640, 360, 'vp9'):
        raise RuntimeError('Upstream canvas/codec changed; manual review required')
    if stream.get('duration_ts') not in (None, 'N/A') and stream.get('time_base'):
        duration = int(stream['duration_ts']) * Fraction(stream['time_base'])
    else:
        duration = Fraction(stream.get('duration') or data.get('format', {}).get('duration', '0'))
    if not 0 < duration <= 60:
        raise RuntimeError('Invalid independent source duration')
    return round(duration * 1000)


def validate_legacy(item):
    if item['clip'] != 'youxian-hengga':
        return
    expected = '1719e2260ab889844c9f6ac271c133b1d11ed91087002627960d74146e08d256'
    if item.get('releaseTag') == 'youxian-hengga-v1' and item['package']['sha256'] != expected:
        raise RuntimeError('Legacy package protection failed')

def main():
    started = time.monotonic()
    parser = argparse.ArgumentParser()
    parser.add_argument('--publish', action='store_true', help='Publish immutable ZIP assets; does not git-push')
    parser.add_argument('--max-actions', type=int, default=0)
    args = parser.parse_args()
    if args.max_actions < 0:
        parser.error('--max-actions must not be negative')
    if args.publish and args.max_actions:
        parser.error('--max-actions is preview-only; partial catalogs must not be published')
    policy = read_json(ROOT / 'sync-policy.json')
    mappings = read_json(ROOT / 'action-map.json')
    legacy = validation.check_legacy(ROOT)
    build_recipe = validation.recipe(ROOT)
    commit, tree = pinned_tree()
    check_licenses(commit, tree, policy)
    sources = sorted((x for x in tree.values() if x['path'].startswith(PREFIX) and x['path'].endswith('.webm')), key=lambda x: x['path'])
    if not sources or len(sources) > 500 or sum(x.get('size', 0) for x in sources) > 512 * 1024 * 1024:
        raise RuntimeError('Empty or unexpectedly large upstream inventory; manual review required')
    if any(x.get('type') != 'blob' or x.get('mode') != '100644' or '/' in x['path'][len(PREFIX):] for x in sources):
        raise RuntimeError('Unexpected nested or non-regular upstream asset')
    catalog_path = ROOT / 'catalog.json'
    catalog = read_json(catalog_path)
    validation.validate_catalog(ROOT, catalog)
    old = {x['clip']: x for x in catalog['actions']}
    seen = set()
    planned = []
    for entry in sources:
        ident, title = stable_id(entry['path'], mappings)
        if ident in seen:
            raise RuntimeError('Duplicate mapped action ID: ' + ident)
        seen.add(ident)
        previous = old.get(ident)
        if previous:
            validate_legacy(previous)
        # The initial legacy clip is deliberately retained byte-exact.
        is_legacy = previous and ident == 'youxian-hengga' and previous.get('releaseTag') == 'youxian-hengga-v1'
        unchanged = previous and previous.get('source', {}).get('gitBlob') == entry['sha'] and previous.get('profile') == PROFILE and previous.get('recipe') == build_recipe and previous.get('title') == title
        planned.append((entry, ident, title, previous, bool(unchanged), bool(is_legacy)))
    print(f'Pinned upstream {commit}; {len(sources)} actions; profile {PROFILE}', flush=True)
    changed = 0
    results = dict(old)
    pending = []
    with tempfile.TemporaryDirectory(prefix='dfy-actions-') as temp:
        temp = Path(temp)
        for entry, ident, title, previous, unchanged, is_legacy in planned:
            if unchanged:
                results[ident]['status'] = 'active'
                continue
            data = source_bytes(commit, entry)
            sha = hashlib.sha256(data).hexdigest()
            if is_legacy and previous['source']['sha256'] == sha:
                copy = json.loads(json.dumps(previous))
                copy['source']['gitBlob'] = entry['sha']
                copy['status'] = 'active'
                copy['build85Compatible'] = True
                results[ident] = copy
                continue
            if is_legacy:
                raise RuntimeError('Legacy v1 source changed; manual review required; old v1 retained')
            if args.max_actions and changed >= args.max_actions:
                continue
            print(f'Convert {ident}: {title}', flush=True)
            source = temp / (ident + '.webm')
            source.write_bytes(data)
            source_duration_ms = independent_source_duration(source)
            output = temp / ident
            # Converter atomically creates output; it must not already exist.
            cmd = [os.sys.executable, str(ROOT / 'scripts' / 'convert_action.py'),
                   '--source', str(source), '--out-dir', str(output), '--clip', ident,
                   '--title', title, '--source-commit', commit, '--source-file', entry['path'],
                   '--license', str(ROOT / 'LICENSE.txt')]
            try:
                process = subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=1200, env=validation.clean_environment())
            except subprocess.CalledProcessError as error:
                raise RuntimeError(f'Conversion failed for {ident}: ' + (error.stderr or '')[-2500:]) from None
            converted = json.loads(process.stdout)
            package = Path(converted['zip']['path'])
            manifest = validation.validated_output(output, package, clip=ident, title=title,
                                                  commit=commit, source_file=entry['path'], source_sha=sha,
                                                  expected_duration_ms=source_duration_ms)
            if converted['source_probe']['duration_ms'] != source_duration_ms:
                raise RuntimeError('Converter source duration differs from independent ffprobe')
            if (output / 'LICENSE.txt').read_bytes() != (ROOT / 'LICENSE.txt').read_bytes():
                raise RuntimeError('Converter package license does not match approved template')
            if converted['source_probe']['alpha_min'] >= 255 or converted['source_probe']['alpha_max'] == 0:
                raise RuntimeError('Decoded transparency/content validation failed: ' + ident)
            # The public revision is addressed by the complete validated ZIP bytes.
            revision = hashlib.sha256(package.read_bytes()).hexdigest()[:16]
            tag = ident + '-12fps-' + revision
            immutable_name = tag + '.zip'
            immutable_zip = temp / immutable_name
            shutil.copyfile(package, immutable_zip)
            relative = f'actions/{ident}/revisions/{revision}'
            destination = validation.safe_destination(ROOT, relative)
            if destination.exists():
                validation.require_plain_tree(destination)
                existing_files = {p.relative_to(destination) for p in destination.rglob('*') if p.is_file()}
                output_files = {p.relative_to(output) for p in output.rglob('*') if p.is_file()}
                if existing_files != output_files or any((destination / p).read_bytes() != (output / p).read_bytes() for p in output_files):
                    raise RuntimeError('Existing immutable frame revision mismatch')
            pending.append((immutable_zip, tag, title + ' · 12fps · ' + revision, output, destination))
            results[ident] = {
                'clip': ident, 'title': title, 'version': revision,
                'format': 'static-webp-frames', 'profile': PROFILE, 'recipe': build_recipe,
                'width': manifest['width'], 'height': manifest['height'],
                'frameCount': manifest['frameCount'],
                'durationMs': sum(f['durationMs'] for f in manifest['frames']),
                'license': 'CC-BY-NC-SA-4.0', 'status': 'active',
                'licenseSha256': hashlib.sha256((output / 'LICENSE.txt').read_bytes()).hexdigest(),
                'build85Compatible': False,
                'manifest': relative + '/manifest.json', 'frames': relative + '/frames/',
                'releaseTag': tag,
                'package': {'url': f'https://github.com/{DEST}/releases/download/{tag}/{immutable_name}',
                            'bytes': immutable_zip.stat().st_size, 'sha256': hashlib.sha256(immutable_zip.read_bytes()).hexdigest()},
                'source': {**manifest['source'], 'gitBlob': entry['sha']},
            }
            changed += 1
        # Hard gate: the complete pinned batch has passed conversion and independent
        # validation before the first Release upload or installed frame write.
        # This gate applies to manual publish AND scheduled incremental runs.
        validation.check_legacy(ROOT)
        print(f'Batch preflight passed: {len(sources)} sources, {changed} staged conversions', flush=True)
        if args.publish:
            for package, tag, title, output, destination in pending:
                release(package, tag, title, publish=True)
                if not destination.exists():
                    destination.mkdir(parents=True)
                    shutil.copytree(output / 'frames', destination / 'frames')
                    for filename in ('manifest.json', 'LICENSE.txt'):
                        shutil.copyfile(output / filename, destination / filename)
    for ident, value in results.items():
        if ident not in seen:
            value['status'] = 'missing-upstream'
    catalog['actions'] = sorted(results.values(), key=lambda x: x['clip'])
    catalog['upstream'] = {'repository': SOURCE_URL, 'commit': commit, 'actionCount': len(sources)}
    catalog['sync'] = {'profile': PROFILE, 'intervalHours': 6, 'deletionPolicy': 'mark-only', 'licenseChangePolicy': 'manual-review'}
    catalog['legacy'] = [legacy]
    validation.check_legacy(ROOT)
    if args.publish:
        validation.validate_catalog(ROOT, catalog)
        write_json(catalog_path, catalog)
        write_json(ROOT / 'upstream-state.json', {'commit': commit, 'sourceCount': len(sources), 'profile': PROFILE})
    else:
        write_json(ROOT / 'sync-preview.json', catalog)
    totals = {'actions': len(catalog['actions']),
              'frames': sum(item['frameCount'] for item in catalog['actions']),
              'zipBytes': sum(item['package']['bytes'] for item in catalog['actions']),
              'elapsedSeconds': round(time.monotonic() - started, 2), 'converted': changed,
              'publish': args.publish, 'upstream': commit}
    write_json(ROOT / 'sync-report.json', totals)
    print(json.dumps(totals, sort_keys=True), flush=True)
    print(f'Validated {len(sources)} upstream actions; converted {changed}; publish={args.publish}', flush=True)
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a', encoding='utf-8') as report:
            report.write(f'## Asset sync\n- Upstream: `{commit}`\n- Sources: {len(sources)}\n- Converted: {changed}\n- Profile: `{PROFILE}`\n- Existing Build85 remains single-action only.\n')

if __name__ == '__main__':
    main()
