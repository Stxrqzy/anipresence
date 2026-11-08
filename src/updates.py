"""Release feed parsing and streamed installer downloads (used off the UI thread)."""
import hashlib
import re
from pathlib import Path
from urllib.parse import urlsplit

import requests

MAX_INSTALLER_BYTES = 300 * 1024 * 1024


def version_tuple(value):
    match = re.fullmatch(r'v?(\d+)\.(\d+)\.(\d+)', str(value).strip())
    if not match:
        raise ValueError('Release version must look like 2.4.0.')
    return tuple(map(int, match.groups()))


def https_url(url):
    parts = urlsplit(str(url))
    if parts.scheme != 'https' or not parts.hostname or parts.username or parts.password:
        raise ValueError('Use an HTTPS release/download URL.')
    return str(url)


def parse_release(data):
    if not isinstance(data, dict) or data.get('draft') or data.get('prerelease'):
        raise ValueError('The feed does not contain a stable release.')
    version = str(data.get('version') or data.get('tag_name') or '').removeprefix('v')
    version_tuple(version)
    url = data.get('url') if 'version' in data else None
    digest = data.get('sha256') or ''
    if not url and 'version' not in data:
        name = f'AniPresence-Setup-{version}.exe'
        asset = next((a for a in data.get('assets', []) if a.get('name') == name), {})
        url = asset.get('browser_download_url') or ''
        digest = asset.get('digest') or ''
    digest = digest.removeprefix('sha256:').lower()
    if digest and not re.fullmatch(r'[0-9a-f]{64}', digest):
        raise ValueError('The release SHA-256 digest is invalid.')
    return {'version': version, 'url': https_url(url) if url else '', 'sha256': digest}


def check_release(feed):
    with requests.get(https_url(feed), timeout=(10, 25), headers={
        'Accept': 'application/json', 'User-Agent': 'AniPresence-updater',
    }) as response:
        if response.status_code == 404:
            raise ValueError('No published release found. The repository/feed must be publicly accessible.')
        response.raise_for_status()
        return parse_release(response.json())


def download_release(release, directory, progress=lambda percent: None):
    version_tuple(release['version'])
    url = https_url(release['url'])
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    dest = directory / f"AniPresence-Setup-{release['version']}.exe"
    partial = dest.with_suffix('.exe.part')
    sha = hashlib.sha256()
    size = 0
    last_percent = -1
    try:
        with requests.get(url, stream=True, timeout=(10, 30)) as response:
            response.raise_for_status()
            https_url(response.url)
            total = int(response.headers.get('Content-Length') or 0)
            if total > MAX_INSTALLER_BYTES:
                raise ValueError('Installer exceeds the download size limit.')
            with partial.open('wb') as output:
                for chunk in response.iter_content(128 * 1024):
                    if not chunk:
                        continue
                    if size == 0 and not chunk.startswith(b'MZ'):
                        raise ValueError('The download is not a Windows installer.')
                    size += len(chunk)
                    if size > MAX_INSTALLER_BYTES:
                        raise ValueError('Installer exceeds the download size limit.')
                    output.write(chunk)
                    sha.update(chunk)
                    percent = min(99, int(size * 100 / total)) if total else 0
                    if percent != last_percent:
                        progress(percent)
                        last_percent = percent
        if size == 0 or (total and size != total):
            raise ValueError('Installer download was incomplete.')
        if release.get('sha256') and sha.hexdigest() != release['sha256']:
            raise ValueError('Installer checksum did not match. Download it again.')
        partial.replace(dest)
        progress(100)
        return dest
    except Exception:
        partial.unlink(missing_ok=True)
        raise
