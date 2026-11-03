#!/usr/bin/env python3
"""Verify preserved dependency sources; pass --remote to check GitHub refs too."""

import argparse
import concurrent.futures
import hashlib
import json
import pathlib
import re
import subprocess
import sys
import time
import urllib.parse

ROOT = pathlib.Path(__file__).resolve().parents[1]


def yaml_file(path):
    return json.loads(subprocess.check_output([
        'ruby', '-ryaml', '-rjson', '-e', 'puts JSON.generate(YAML.load_file(ARGV[0]))', str(path),
    ], text=True))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--remote', action='store_true')
    args = parser.parse_args()
    manifest = json.loads(ROOT.joinpath('tool/dependency_sources.json').read_text())
    spec = yaml_file(ROOT / 'pubspec.yaml')
    lock = yaml_file(ROOT / 'pubspec.lock')['packages']
    owner_prefix = 'https://github.com/' + manifest['github_owner'] + '/'
    remotes = {}

    for section in ['dependencies', 'dev_dependencies', 'dependency_overrides']:
        for name, declaration in spec.get(section, {}).items():
            if not isinstance(declaration, dict) or 'git' not in declaration:
                continue
            source = declaration['git']
            if not isinstance(source, dict) or not source['url'].startswith(owner_prefix):
                raise ValueError('External Git declaration: ' + section + '/' + name)
            if not re.fullmatch('[0-9a-f]{40}', source.get('ref', '')):
                raise ValueError('Floating Git declaration: ' + section + '/' + name)

    git_packages = {name for name, package in lock.items() if package['source'] == 'git'}
    if git_packages != set(manifest['packages']):
        raise ValueError('Git dependency inventory differs from the preserved manifest')
    for name, expected in manifest['packages'].items():
        package = lock[name]
        actual = package['description']
        if package['version'] != expected['version']:
            raise ValueError('Package version changed: ' + name)
        for field, value in [('url', expected['url']), ('ref', expected['ref']),
                             ('resolved-ref', expected['ref']), ('path', expected['path'])]:
            if actual.get(field) != value:
                raise ValueError('Preserved source differs: ' + name + '/' + field)
        override = spec['dependency_overrides'][name]['git']
        if override.get('url') != expected['url'] or override.get('ref') != expected['ref'] or override.get('path', '.') != expected['path']:
            raise ValueError('Missing transitive source pin: ' + name)
        remotes.setdefault(expected['url'], {})['refs/heads/' + expected['preservation_branch']] = expected['ref']

    sdk = manifest['flutter_sdk']
    remotes.setdefault(sdk['url'], {})['refs/heads/' + sdk['preservation_branch']] = sdk['ref']
    for source in manifest['additional_sources'].values():
        remotes.setdefault(source['url'], {})['refs/heads/' + source['preservation_branch']] = source['ref']
    podfile = ROOT / 'ios/Podfile'
    pod_lock = yaml_file(ROOT / 'ios/Podfile.lock')
    wescan = manifest['additional_sources']['WeScan']
    if wescan['ref'] not in podfile.read_text() or wescan['url'] not in podfile.read_text():
        raise ValueError('WeScan Podfile source differs')
    if pod_lock['PODFILE CHECKSUM'] != hashlib.sha1(podfile.read_bytes()).hexdigest():
        raise ValueError('Podfile checksum differs')
    for name, expected in manifest.get('native_pods', {}).items():
        recipe = json.loads(ROOT.joinpath(expected['spec_path']).read_text())
        if recipe['version'] != expected['version'] or recipe['source'] != {
                'http': expected['archive_url'], 'sha256': expected['archive_sha256']}:
            raise ValueError('Preserved native recipe differs: ' + name)
        if not expected['archive_url'].startswith(owner_prefix):
            raise ValueError('External native archive: ' + name)
        relative = pathlib.Path(expected['spec_path']).relative_to('ios').as_posix()
        for subspec in expected['subspecs']:
            if "pod '%s', :podspec => '%s'" % (subspec, relative) not in podfile.read_text():
                raise ValueError('Missing preserved native declaration: ' + subspec)
        external = pod_lock['EXTERNAL SOURCES'].get(name, {})
        if external.get(':podspec') != relative:
            raise ValueError('Native lock source differs: ' + name)
        remotes.setdefault(expected['url'], {})['refs/heads/' + expected['preservation_branch']] = expected['ref']
    ruby = manifest.get('ruby_toolchain')
    if ruby and hashlib.sha256(ROOT.joinpath(ruby['lockfile']).read_bytes()).hexdigest() != ruby['lockfile_sha256']:
        raise ValueError('Ruby toolchain lockfile differs from the preserved manifest')

    config = ROOT / '.dart_tool/package_config.json'
    if config.exists():
        packages = json.loads(config.read_text())['packages']
        roots = {
            item['name']: pathlib.Path(urllib.parse.unquote(urllib.parse.urlparse(item['rootUri']).path))
            if item['rootUri'].startswith('file:') else (config.parent / item['rootUri']).resolve()
            for item in packages
        }
        for binary in manifest['preserved_binaries']:
            file = roots[binary['package']] / binary['path']
            if hashlib.sha256(file.read_bytes()).hexdigest() != binary['sha256']:
                raise ValueError('Preserved native binary differs: ' + binary['package'] + '/' + binary['path'])

    if args.remote:
        def check_remote(pair):
            url, expected = pair
            for attempt in range(3):
                try:
                    output = subprocess.check_output([
                        'git', '-c', 'http.version=HTTP/1.1', 'ls-remote', url,
                        'refs/heads/woodbox/*',
                    ], text=True, timeout=60)
                    break
                except (subprocess.SubprocessError, OSError):
                    if attempt == 2:
                        raise
                    time.sleep(attempt + 1)
            actual = dict(line.split()[::-1] for line in output.splitlines())
            if any(actual.get(ref) != sha for ref, sha in expected.items()):
                raise ValueError('Missing preserved remote commit: ' + url)
            return url
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
            for _ in executor.map(check_remote, remotes.items()):
                pass
    print('Verified %d pinned Git packages, %d native recipes, preserved binaries, WeScan, and Flutter SDK%s.' % (
        len(git_packages), len(manifest.get('native_pods', {})),
        ' across %d remote repositories' % len(remotes) if args.remote else '',
    ))


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1)
