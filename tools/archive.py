#!/usr/bin/env python3
"""Preserve Colors! originals and stream the legacy C renderer into ffmpeg."""
from __future__ import annotations
import argparse
import datetime as dt
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
ARCHIVE = ROOT / 'archive' / 'fra'
BINARY = ROOT / 'build' / 'replay'
SITE = 'https://www.colorslive.com'


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def write_json(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(path, (json.dumps(obj, ensure_ascii=False, indent=2) + '\n').encode())


def atomic_write(path, data):
    tmp = path.with_name(path.name + '.partial')
    tmp.write_bytes(data)
    tmp.replace(path)


def fetch(url):
    for attempt in range(4):
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'colors-drawings-preservation/1.0', 'Accept-Encoding': 'identity'})
            with urllib.request.urlopen(req, timeout=60) as response:
                body = response.read()
                return body, {'url': url, 'final_url': response.url, 'retrieved_at': now(),
                              'status': response.status, 'headers': dict(response.headers)}
        except urllib.error.HTTPError as error:
            if error.code not in (429, 500, 502, 503, 504) or attempt == 3:
                raise
        except (OSError, TimeoutError):
            if attempt == 3:
                raise
        time.sleep(1 + attempt)
    raise RuntimeError('unreachable')


def drw_header(data):
    if len(data) < 64 or data[:4] != b'!loC':
        raise ValueError('invalid DRW header')
    values = struct.unpack_from('<16I', data)
    version = values[1]
    if version not in (1002, 1071):
        raise ValueError(f'unsupported DRW version {version}')
    if len(data) != 64 + values[15] * 4:
        raise ValueError('DRW command count does not match file length')
    commands = [c for c, in struct.iter_unpack('<I', data[64:])]
    return {'magic': '!loC', 'version': version, 'original_colors_version': values[2],
            'last_colors_version': values[3], 'stroke_count': values[4],
            'drawing_seconds_raw': values[5], 'save_count': values[6], 'platform': values[7],
            'aspect_ratio_raw': struct.unpack_from('<f', data, 32)[0],
            'header_bytes': 64, 'command_count': values[15],
            'command_counts': {name: sum(c & 3 == i for c in commands)
                               for i, name in enumerate(('point', 'pen_up', 'color', 'brush'))},
            'header_words_le': list(values)}


def png_size(data):
    if data[:8] != b'\x89PNG\r\n\x1a\n' or len(data) < 24:
        raise ValueError('invalid PNG')
    return struct.unpack_from('>II', data, 16)


def catalog():
    return json.loads((ARCHIVE / 'catalog.json').read_text())


def seal():
    files = sorted(p for p in ARCHIVE.rglob('*') if p.is_file() and p.name not in ('SHA256SUMS', '.DS_Store')
                   and '.partial' not in p.name)
    atomic_write(ARCHIVE / 'SHA256SUMS', ''.join(
        f'{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.relative_to(ARCHIVE).as_posix()}\n'
        for p in files).encode())


def verify():
    manifest = ARCHIVE / 'SHA256SUMS'
    if not manifest.exists():
        raise ValueError('archive has no SHA256SUMS')
    checked = set()
    for line in manifest.read_text().splitlines():
        digest, relative = line.split('  ', 1)
        path = (ARCHIVE / relative).resolve()
        if not path.is_relative_to(ARCHIVE.resolve()) or path in checked:
            raise ValueError(f'unsafe or duplicate manifest path: {relative}')
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError(f'checksum mismatch: {relative}')
        checked.add(path)
    actual = {p.resolve() for p in ARCHIVE.rglob('*') if p.is_file() and p.name not in ('SHA256SUMS', '.DS_Store')}
    if actual != checked:
        raise ValueError(f'manifest coverage mismatch: {actual ^ checked}')
    c = catalog()
    expected = {p['pid'] for p in json.loads((ARCHIVE / 'discovery.json').read_text())['artist']['data']['paintings']}
    if {p['id'] for p in c['paintings']} != expected:
        raise ValueError('catalog does not cover the gallery')
    for entry in c['paintings']:
        folder = ARCHIVE / str(entry['id'])
        header = drw_header((folder / 'drawing.drw').read_bytes())
        if header != entry['drw']:
            raise ValueError(f"DRW metadata mismatch: {entry['id']}")
        if list(png_size((folder / 'original.png').read_bytes())) != entry['image_size']:
            raise ValueError(f"PNG dimensions mismatch: {entry['id']}")
        if json.loads((folder / 'metadata.json').read_text())['username'] != 'fra':
            raise ValueError('unexpected owner')
    print(f"Verified {len(checked)} files and {len(c['paintings'])} drawings.")


def discover(browser_path=None):
    """Use the public site's own URL resolution; hashed CDN names are not guessed."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as error:
        raise RuntimeError('sync needs Playwright: pip install -r requirements-sync.txt; python -m playwright install chromium') from error
    filenames = {}
    with sync_playwright() as pw:
        options = {'headless': True, 'args': ['--enable-webgl', '--use-angle=swiftshader']}
        if browser_path:
            options['executable_path'] = browser_path
        browser = pw.chromium.launch(**options)
        page = browser.new_page()
        def request(req):
            match = re.search(r'/(\d+)-([A-Za-z0-9_-]+)\.(?:jpg|png|drw)(?:\?|$)', req.url)
            if match:
                filenames[int(match[1])] = f'{match[1]}-{match[2]}'
        page.on('request', request)
        page.goto(SITE + '/artist/fra', wait_until='domcontentloaded')
        discovery = page.evaluate('''async () => {
            const ws = new WebSocket('wss://www.colorslive.com/_1275');
            await new Promise((resolve, reject) => {ws.onopen=resolve; ws.onerror=reject;});
            let id=0;
            const call = (type, params) => new Promise((resolve, reject) => {
                const n=++id;
                const timer=setTimeout(() => {ws.removeEventListener('message', receive);reject(new Error('API timeout'));},30000);
                function receive(event) {
                    const result=JSON.parse(event.data);
                    if(result.id !== n) return;
                    clearTimeout(timer); ws.removeEventListener('message', receive);
                    result.ok ? resolve(result) : reject(new Error(JSON.stringify(result)));
                }
                ws.addEventListener('message',receive); ws.send(JSON.stringify({type,id:n,params}));
            });
            try {
                const init=await call('init',{system:22,version:1300,language:'en'});
                const artist=await call('artist.get',{username:'fra'});
                const paintings=[];
                for(const {pid} of artist.data.paintings) paintings.push({response:await call('painting.get',{pid})});
                return {init,artist,paintings};
            } finally {ws.close();}
        }''')
        ids = [item['pid'] for item in discovery['artist']['data']['paintings']]
        # Allow the application's gallery requests to finish. A per-painting route
        # also handles images which the gallery loads only after scrolling.
        for pid in ids:
            if pid not in filenames:
                with page.expect_request(lambda req: bool(re.search(rf'/{pid}-[\w-]+\.jpg', req.url)), timeout=45000):
                    page.goto(SITE + f'/painting/{pid}', wait_until='domcontentloaded')
        browser.close()
    for painting in discovery['paintings']:
        painting['filename'] = filenames[painting['response']['data']['id']]
    discovery.update(source=SITE + '/artist/fra', retrieved_at=now())
    return discovery


def sync(args):
    """Keep the previous archive usable if a network request or validation fails."""
    global ARCHIVE
    original = ARCHIVE
    if (original / 'catalog.json').exists():
        verify()
    with tempfile.TemporaryDirectory(prefix='colors-sync-', dir=ROOT / 'archive') as tmp:
        staged = Path(tmp) / 'fra'
        shutil.copytree(original, staged) if original.exists() else staged.mkdir()
        try:
            ARCHIVE = staged
            sync_staged(args)
        finally:
            ARCHIVE = original
        backup = Path(tmp) / 'previous'
        if original.exists():
            original.rename(backup)
        try:
            staged.rename(original)
        except BaseException:
            if backup.exists():
                backup.rename(original)
            raise


def sync_staged(args):
    discovery = discover(args.browser)
    gallery_bytes, gallery_receipt = fetch(SITE + '/_1231/artist/fra')
    gallery = json.loads(gallery_bytes)
    rotations = {p['id']: p.get('o', 0) for p in gallery['paintings'] if p['id']}
    discovered_ids = {p['response']['data']['id'] for p in discovery['paintings']}
    if discovered_ids != set(rotations):
        raise ValueError('gallery APIs disagree about the painting list; refusing an incomplete archive')
    entries = []
    for painting in discovery['paintings']:
        meta = painting['response']['data']; pid = meta['id']
        if meta['username'] != 'fra' or meta.get('isDeleted') or meta.get('isPrivate'):
            raise ValueError(f'invalid public painting: {pid}')
        folder = ARCHIVE / str(pid); folder.mkdir(parents=True, exist_ok=True)
        for kind, ext, name in [('drw','drw','drawing.drw'), ('png','png','original.png'),
                                ('jpg_1280x720','jpg','preview.jpg')]:
            path = folder / name
            if not path.exists():
                url = f"https://cdn.colorslive.com/{kind}/{painting['filename']}.{ext}"
                body, receipt = fetch(url)
                if kind == 'drw':
                    drw_header(body)
                elif kind == 'png':
                    png_size(body)
                elif not body.startswith(b'\xff\xd8'):
                    raise ValueError(f'not a JPEG: {url}')
                atomic_write(path, body); write_json(folder / (name + '.http.json'), receipt)
                print(f'{pid}: downloaded {name}', flush=True)
        details, receipt = fetch(SITE + f'/_1231/details/{pid}')
        json.loads(details)
        atomic_write(folder / 'details.json', details)
        write_json(folder / 'details.json.http.json', receipt)
        write_json(folder / 'metadata.json', meta)
        entries.append(make_entry(meta, painting['filename'], rotations.get(pid, 0)))
    write_json(ARCHIVE / 'discovery.json', discovery)
    atomic_write(ARCHIVE / 'gallery.json', gallery_bytes)
    write_json(ARCHIVE / 'gallery.json.http.json', gallery_receipt)
    write_json(ARCHIVE / 'catalog.json', {'schema_version': 1, 'artist': 'fra',
               'source': discovery['source'], 'retrieved_at': discovery['retrieved_at'], 'paintings': entries})
    seal(); verify()


def make_entry(meta, filename, rotation):
    folder = ARCHIVE / str(meta['id'])
    return {'id': meta['id'], 'title': meta['title'], 'description': meta['description'],
            'author': meta['username'], 'published_at': dt.datetime.fromtimestamp(meta['dateAddedU'], dt.timezone.utc).isoformat(),
            'source': SITE + f"/painting/{meta['id']}", 'cdn_basename': filename,
            'rotation_clockwise_quarters': rotation, 'image_size': list(png_size((folder / 'original.png').read_bytes())),
            'drw': drw_header((folder / 'drawing.drw').read_bytes())}


def build():
    compiler = shutil.which(os.environ.get('CC', 'cc'))
    if not compiler:
        raise RuntimeError('C compiler missing; install cc/clang/gcc or set CC')
    sources = [ROOT / 'src' / name for name in ('replay.c', 'brushes.h')]
    if not BINARY.exists() or any(p.stat().st_mtime_ns > BINARY.stat().st_mtime_ns for p in sources):
        BINARY.parent.mkdir(exist_ok=True)
        subprocess.run([compiler, '-std=c11', '-O3', '-Wall', '-Wextra', '-Wpedantic', str(sources[0]), '-lm', '-o', str(BINARY)], check=True)
    return BINARY


def select(ids):
    entries = catalog()['paintings']
    if ids == ['all']:
        return entries
    wanted = {int(i) for i in ids}
    if wanted - {e['id'] for e in entries}:
        raise ValueError(f'unknown drawing IDs: {wanted}')
    return [e for e in entries if e['id'] in wanted]


def render(args):
    ffmpeg = shutil.which('ffmpeg')
    if not ffmpeg:
        raise RuntimeError('ffmpeg missing')
    binary = build()
    args.output.mkdir(parents=True, exist_ok=True)
    for entry in select(args.ids):
        pid = entry['id']; folder = ARCHIVE / str(pid)
        target = args.output / f'{pid}.mp4'
        if target.exists() and not args.overwrite:
            raise ValueError(f'{target} already exists; use --overwrite')
        # --width is the displayed width; follow the original PNG's aspect ratio.
        display_w = args.width
        display_h = max(2, round(display_w * entry['image_size'][1] / entry['image_size'][0] / 2) * 2)
        if display_h > 8192:
            raise ValueError('resulting height exceeds 8192 pixels; choose a smaller --width')
        rotation = entry['rotation_clockwise_quarters'] % 4
        w, h = (display_h, display_w) if rotation % 2 else (display_w, display_h)
        count = entry['drw']['command_count']
        frames = count + 1 if args.every_command else max(2, round(args.seconds * args.fps))
        hold = round(args.hold * args.fps)
        temp = target.with_name(f'{pid}.partial.mp4')
        ppm = args.output / f'{pid}.partial.ppm'
        filters = {0: None, 1: 'transpose=clock', 2: 'hflip,vflip', 3: 'transpose=cclock'}
        encoder_args = [ffmpeg, '-hide_banner', '-loglevel', 'error', '-y', '-f', 'rawvideo', '-pixel_format', 'rgb24',
                        '-video_size', f'{w}x{h}', '-framerate', str(args.fps), '-i', 'pipe:0']
        if filters[rotation]:
            encoder_args += ['-vf', filters[rotation]]
        encoder_args += ['-an', '-c:v', 'libx264', '-preset', 'fast', '-crf', str(args.crf), '-pix_fmt', 'yuv420p',
                         '-movflags', '+faststart', '-metadata', f"title={entry['title']}",
                         '-metadata', f"artist={entry['author']}", '-metadata', f"comment={entry['source']}", str(temp)]
        print(f"Rendering {pid} — {entry['title']}: {frames + hold} frames, {display_w}×{display_h}", flush=True)
        producer = subprocess.Popen([str(binary), str(folder / 'drawing.drw'), str(w), str(h), str(frames), str(hold), str(ppm)], stdout=subprocess.PIPE)
        encoder = None
        try:
            encoder = subprocess.Popen(encoder_args, stdin=producer.stdout)
            producer.stdout.close()
            encoder_status = encoder.wait(); producer_status = producer.wait()
            if producer_status or encoder_status:
                raise RuntimeError(f'render failed: C={producer_status}, ffmpeg={encoder_status}')
            # Rotate the lossless final C frame through ffmpeg as well.
            final_tmp = target.with_name(f'{pid}.partial.png')
            png_args = [ffmpeg, '-hide_banner', '-loglevel', 'error', '-y', '-i', str(ppm)]
            if filters[rotation]:
                png_args += ['-vf', filters[rotation]]
            subprocess.run(png_args + ['-frames:v', '1', str(final_tmp)], check=True)
            temp.replace(target); final_tmp.replace(args.output / f'{pid}.png')
            write_json(args.output / f'{pid}.render.json', {'id': pid, 'title': entry['title'], 'rendered_at': now(),
                       'input_sha256': hashlib.sha256((folder / 'drawing.drw').read_bytes()).hexdigest(),
                       'renderer_sha256': hashlib.sha256((ROOT / 'src/replay.c').read_bytes()).hexdigest(),
                       'brushes_sha256': hashlib.sha256((ROOT / 'src/brushes.h').read_bytes()).hexdigest(),
                       'fps': args.fps, 'frames': frames + hold, 'every_command': args.every_command,
                       'display_size': [display_w, display_h], 'encoder_command': encoder_args,
                       'video_sha256': hashlib.sha256(target.read_bytes()).hexdigest(),
                       'fidelity': 'software reconstruction; see docs/FORMAT.md'})
        finally:
            if encoder is not None and encoder.poll() is None:
                encoder.terminate(); encoder.wait()
            if producer.stdout is not None:
                producer.stdout.close()
            if producer.poll() is None:
                producer.terminate(); producer.wait()
            for path in (temp, ppm, target.with_name(f'{pid}.partial.png')):
                path.unlink(missing_ok=True)


def positive_float(value):
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError('must be positive and finite')
    return number


def nonnegative_float(value):
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise argparse.ArgumentTypeError('must be nonnegative and finite')
    return number


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('verify', help='verify checksums, file structure and gallery completeness')
    commands.add_parser('list', help='list archived drawings')
    commands.add_parser('build', help='compile the C renderer')
    s = commands.add_parser('sync', help='discover the gallery and archive drawings sequentially')
    s.add_argument('--browser', help='optional existing Chromium executable')
    r = commands.add_parser('render', help='stream C RGB frames into ffmpeg')
    r.add_argument('ids', nargs='+', help='painting IDs, or all')
    r.add_argument('--output', type=Path, default=ROOT / 'videos')
    r.add_argument('--width', type=int, default=1024)
    r.add_argument('--seconds', type=positive_float, default=60)
    r.add_argument('--fps', type=int, default=30)
    r.add_argument('--hold', type=nonnegative_float, default=2)
    r.add_argument('--crf', type=int, default=18)
    r.add_argument('--every-command', action='store_true', help='emit one frame after each DRW command (overrides --seconds)')
    r.add_argument('--overwrite', action='store_true')
    args = parser.parse_args()
    try:
        if args.command == 'verify':
            verify()
        elif args.command == 'list':
            for e in catalog()['paintings']:
                print(f"{e['id']:>6}  {e['published_at'][:10]}  {e['drw']['command_count']:>6} commands  {e['title']}")
        elif args.command == 'build':
            print(build())
        elif args.command == 'sync':
            sync(args)
        elif args.command == 'render':
            if args.width < 2 or args.width > 8192 or args.width % 2 or not 1 <= args.fps <= 240 or not 0 <= args.crf <= 51:
                parser.error('width must be even, 2..8192; fps 1..240; crf 0..51')
            render(args)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        parser.exit(1, f'Error: {error}\n')


if __name__ == '__main__':
    main()
