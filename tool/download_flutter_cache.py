#!/usr/bin/env python3
"""Download and verify the preserved SDK cache, with resumable part caching."""

import hashlib
import json
import pathlib
import shutil
import sys
import time
import urllib.request


def digest(file):
    value = hashlib.sha256()
    with file.open('rb') as source:
        for block in iter(lambda: source.read(1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


def main():
    manifest_file, archive_file, cache_directory = map(pathlib.Path, sys.argv[1:])
    manifest = json.loads(manifest_file.read_text())
    cache = manifest['flutter_sdk']['cache']
    parts = cache.get('parts') or [{'url': cache['url'], 'sha256': cache['sha256'], 'size': cache['size']}]
    directory = cache_directory / cache['sha256']
    directory.mkdir(parents=True, exist_ok=True)
    ready = []
    for index, part in enumerate(parts):
        if not part['url'].startswith('https://github.com/' + manifest['github_owner'] + '/'):
            raise ValueError('SDK cache URL is outside the preserved account')
        file = directory / ('%03d-' % index + part['sha256'])
        if not file.exists() or file.stat().st_size != part['size'] or digest(file) != part['sha256']:
            partial = file.with_suffix('.partial')
            for attempt in range(4):
                try:
                    with urllib.request.urlopen(part['url'], timeout=120) as source, partial.open('wb') as target:
                        shutil.copyfileobj(source, target, 1024 * 1024)
                    if partial.stat().st_size != part['size'] or digest(partial) != part['sha256']:
                        raise ValueError('SDK cache part checksum mismatch')
                    partial.replace(file)
                    break
                except Exception:
                    partial.unlink(missing_ok=True)
                    if attempt == 3:
                        raise
                    time.sleep(2 * (attempt + 1))
        ready.append(file)
        print('SDK cache part %d/%d verified' % (index + 1, len(parts)), file=sys.stderr)
    with archive_file.open('wb') as target:
        for file in ready:
            with file.open('rb') as source:
                shutil.copyfileobj(source, target, 1024 * 1024)
    if archive_file.stat().st_size != cache['size'] or digest(archive_file) != cache['sha256']:
        raise ValueError('Complete SDK cache checksum mismatch')


if __name__ == '__main__':
    main()
