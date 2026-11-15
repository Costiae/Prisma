"""Image providers, local tool installation and LoRA datasets for Prisma."""

import base64
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import platform
import re
import secrets
import shutil
import stat
import subprocess
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import zipfile

from image_jobs import Jobs, JOB_ID, write_json


SD_ARCHIVE = 'sd-master-b521db4-bin-win-vulkan-x64.zip'
SD_URL = f'https://github.com/leejet/stable-diffusion.cpp/releases/download/master-1098-b521db4/{SD_ARCHIVE}'
SD_HASH = '39f90af4f4d407750c06974f2a58e187feb2fa8b284436ea3a8756fcd6f34d39'
BASE_NAME = 'Anything-V3.0-pruned-fp16.safetensors'
BASE_URL = f'https://huggingface.co/admruul/anything-v3.0/resolve/3d14c2790f2f04b631bcb8f939b78fb5fde275d3/{BASE_NAME}'
BASE_HASH = 'ed376204fb1e93cde85757de92c9f737dfb53cef97e7fadb33b99f94e2423469'
UV_ARCHIVE = 'uv-x86_64-pc-windows-msvc.zip'
UV_VERSION = '0.11.19'
UV_URL = f'https://github.com/astral-sh/uv/releases/download/{UV_VERSION}/{UV_ARCHIVE}'
UV_HASH = '1665fc8e37b5d70a134820d6d7891747471a2ac8bc940ee7af0b69fd03b28d61'
TRAIN_PYTHON = '3.10.20'
TRAIN_LOCK = Path(__file__).with_name('requirements-lora-win-cu124.txt')
TRAINER_REV = 'cb70b96858477e716a2096292ab8aab433277f38'
TRAINER_URL = f'https://codeload.github.com/kohya-ss/sd-scripts/zip/{TRAINER_REV}'
TRAINER_HASH = '13587ab358c5ecb878a991a391ca924cb37ff1d427805b1476f41bbf23fcc493'
TOKENIZER = 'openai_clip-vit-large-patch14'
TOKENIZER_FILES = {
    'merges.txt': '9fd691f7c8039210e0fced15865466c65820d09b63988b0174bfe25de299051a',
    'special_tokens_map.json': 'f8c0d6c39aee3f8431078ef6646567b0aba7f2246e9c54b8b99d55c22b707cbf',
    'tokenizer_config.json': 'deef455e52fa5e8151e339add0582e4235f066009601360999d3a9cda83b1129',
    'tokenizer.json': 'a83e0809aa4c3af7208b2df632a7a69668c6d48775b3c3fe4e1b1199d1f8b8f4',
    'vocab.json': '3f0c4f7d2086b61b38487075278ea9ed04edb53a03cbb045b86c27190fa8fb69',
}
CLIP_URL = 'https://huggingface.co/openai/clip-vit-large-patch14/resolve/32bd64288804d66eefd0ccbe215aa642df71cc41'
ONLINE_MODELS = ('gpt-image-1.5', 'gpt-image-2.5-flare', 'gpt-image-1-mini')
ONLINE_SIZES = ('1024x1024', '1024x1536', '1536x1024')
SAMPLERS = ('euler_a', 'euler', 'dpm++2m')
IMAGE_TYPES = {'.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.webp': 'image/webp'}
MAX_UPLOAD = 45 * 1024 * 1024
SLUG = re.compile(r'[a-zA-Z0-9][a-zA-Z0-9_-]{0,47}')
LORA_STEM = re.compile(r'[\w][\w. -]{0,127}')


def string(value, label, limit, *, required=True):
    if not isinstance(value, str) or len(value) > limit or '\0' in value or (required and not value.strip()):
        raise ValueError(f'请填写有效的{label}（最多 {limit} 字）。')
    return value.strip()


def number(value, label, low, high, *, integer=False):
    if (type(value) not in ((int,) if integer else (int, float))
            or not low <= value <= high or not math.isfinite(value)):
        raise ValueError(f'{label}须为 {low}–{high} 之间的{"整数" if integer else "数字"}。')
    return value


def choice(value, options, label):
    if not isinstance(value, str) or value not in options:
        raise ValueError(f'请选择有效的{label}。')
    return value


def filename(name):
    if (not isinstance(name, str) or not 0 < len(name) <= 160 or name != name.lstrip(' ').rstrip(' .')
            or any(c in '<>:"/\\|?*' or ord(c) < 32 for c in name) or PureWindowsPath(name).is_reserved()):
        raise ValueError('文件名无效。')
    return name


def under(root, path):
    return path.resolve().is_relative_to(root.resolve())


def child_file(root, name):
    path = root / filename(name)
    if not under(root, path) or not path.is_file():
        raise FileNotFoundError('文件不存在。')
    return path


def image_bytes(blob, suffix):
    valid = (blob.startswith(b'\x89PNG\r\n\x1a\n') if suffix == '.png' else
             blob.startswith(b'\xff\xd8\xff') if suffix in ('.jpg', '.jpeg') else
             blob[:4] == b'RIFF' and blob[8:12] == b'WEBP' if suffix == '.webp' else False)
    if not valid:
        raise ValueError('图片内容与扩展名不符；请选择 PNG、JPEG 或 WebP。')
    return blob


def decode_blob(data):
    if not isinstance(data, str) or len(data) > MAX_UPLOAD * 4 // 3 + 4:
        raise ValueError('上传内容无效或超过 45 MiB。')
    try:
        blob = base64.b64decode(data, validate=True)
    except ValueError as error:
        raise ValueError('上传内容无效。') from error
    if len(blob) > MAX_UPLOAD:
        raise ValueError('上传内容超过 45 MiB。')
    return blob


def first_file(*paths):
    return next((path for path in paths if path.is_file()), None)


def sha256_file(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def fetch_file(job, url, target, *, sha256=None, limit, label):
    job.phase(label)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_suffix(target.suffix + '.part')
    digest = hashlib.sha256()
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Prisma-Image-Toolkit/1.0'})
        with urllib.request.urlopen(req, timeout=40) as response, temp.open('wb') as stream:
            expected = response.headers.get('Content-Length', '')
            total = int(expected) if expected.isdecimal() else None
            if total and total > limit:
                raise ValueError(f'{label}：文件超过大小限制。')
            count = 0
            last = 0
            read = getattr(response, 'read1', response.read)
            while chunk := read(1024 * 1024):
                job.check()
                count += len(chunk)
                if count > limit:
                    raise ValueError(f'{label}：文件超过大小限制。')
                digest.update(chunk)
                stream.write(chunk)
                if count - last >= 8 * 1024 * 1024:
                    job.update(progress={'bytes': count, 'total': total})
                    last = count
            if not count or (total is not None and total != count):
                raise ValueError(f'{label}：文件下载不完整，请重试。')
        if sha256 and digest.hexdigest() != sha256:
            raise ValueError(f'{label}：SHA-256 与发布文件不符。')
        job.check()
        temp.replace(target)
    except (urllib.error.URLError, TimeoutError) as error:
        raise ValueError(f'{label}失败：{error.reason if isinstance(error, urllib.error.URLError) else "连接超时"}。') from error
    finally:
        temp.unlink(missing_ok=True)


def unpack(job, archive, directory, *, strip_root=None, only=None, limit=240 * 1024 * 1024):
    """Copy ordinary files only, optionally stripping a known archive root."""
    with zipfile.ZipFile(archive) as source:
        members = source.infolist()
        if len(members) > 4000 or sum(entry.file_size for entry in members) > limit:
            raise ValueError('工具压缩包大小异常。')
        for entry in members:
            job.check()
            if entry.is_dir():
                continue
            parts = PurePosixPath(entry.filename).parts
            if strip_root:
                if not parts or parts[0] != strip_root:
                    raise ValueError('工具压缩包目录与预期不符。')
                parts = parts[1:]
            if only is not None:
                if not parts or parts[-1] not in only:
                    continue
                parts = parts[-1:]
            if not parts or any(part in ('.', '..') for part in parts) or stat.S_ISLNK(entry.external_attr >> 16):
                raise ValueError('工具压缩包包含无效路径。')
            path = directory.joinpath(*(filename(part) for part in parts))
            path.parent.mkdir(parents=True, exist_ok=True)
            with source.open(entry) as read, path.open('xb') as write:
                shutil.copyfileobj(read, write)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class ImageToolkit:
    def __init__(self, root, *, workspace=None, legacy_home=None, reference=None, use_existing=True):
        self.root = root
        self.home = (workspace or root / 'build' / 'prisma-images').resolve()
        self.legacy = legacy_home or Path(os.environ.get('PRISMA_MEDIA_HOME', 'E:/LLM/prisma-media'))
        self.pilot = root / 'build' / 'prisma-style-lora'
        self.reference = (reference or root / 'Reference' / 'Img').resolve()
        self.use_existing = use_existing
        self.downloads = self.home / 'downloads'
        self.tools = self.home / 'tools'
        self.models = self.home / 'models'
        self.dataset_root = self.home / 'datasets'
        self.lora_root = self.home / 'loras'
        self.jobs = Jobs(self.home / 'jobs')
        self.identities = {}
        self.windows = os.name == 'nt' and platform.machine().lower() in ('amd64', 'x86_64')

    def engine(self):
        installed = self.tools / 'sd-cpp' / 'sd-cli.exe'
        return first_file(installed, self.legacy / 'stable-diffusion.cpp' / 'sd-cli.exe') if self.use_existing else first_file(installed)

    def base(self):
        installed = self.models / BASE_NAME
        return first_file(installed, self.legacy / BASE_NAME) if self.use_existing else first_file(installed)

    def trainer(self):
        parents = (self.tools, self.pilot) if self.use_existing else (self.tools,)
        for parent in parents:
            code = parent / 'sd-scripts'
            python = parent / 'venv' / 'Scripts' / 'python.exe'
            cache = parent / 'tokenizer-cache'
            ready = parent == self.pilot
            if parent == self.tools:
                try:
                    marker = json.loads((parent / 'trainer-ready.json').read_text(encoding='utf-8'))
                    ready = marker.get('lock_sha256') == sha256_file(TRAIN_LOCK) and marker.get('python') == TRAIN_PYTHON
                except (OSError, ValueError, AttributeError):
                    pass
            if ready and python.is_file() and (code / 'train_network.py').is_file() and all(
                (cache / TOKENIZER / name).is_file() for name in TOKENIZER_FILES
            ):
                return python, code, cache
        return None

    def loras(self):
        found = {}
        previous = (('inference-lora', 'R1'), ('output-v2', 'R2'), ('output-v3', 'R3')) if self.use_existing else ()
        for directory, run in previous:
            for path in sorted((self.pilot / directory).glob('*.safetensors')):
                if not path.is_file() or not under(self.pilot / directory, path) or not LORA_STEM.fullmatch(path.stem):
                    continue
                step = re.search(r'-step(\d+)$', path.stem)
                label = f'{run} · {int(step[1])} 步' if step else f'{run} · 最终权重'
                key = f'{run.lower()}-{int(step[1])}' if step else f'{run.lower()}-final'
                found[key] = (path, label, 0.45 if run == 'R1' else 0.35)
        for path in sorted(self.lora_root.glob('*.safetensors')):
            if path.is_file() and under(self.lora_root, path) and LORA_STEM.fullmatch(path.stem):
                found[f'import:{path.stem}'] = (path, path.stem, 0.35)
        for record in sorted(self.jobs.root.glob('*/job.json'), reverse=True):
            try:
                job = self.jobs.get(record.parent.name)
                if job['state'] != 'done' or job['kind'] != 'train':
                    continue
                for item in job['checkpoints']:
                    path = child_file(record.parent / 'checkpoints', item['name'])
                    found[f'{job["id"]}:{path.stem}'] = (path, item['label'], 0.35)
            except (OSError, ValueError):
                continue
        return found

    def sources(self):
        result = {}
        if self.reference.is_dir():
            result['reference'] = (self.reference, f'参考目录 · {self.reference.name}')
        previous = (
            ('r2', 'R2 原训练集', self.pilot / 'dataset' / '10_prismacel'),
            ('r3', 'R3 原训练集', self.pilot / 'dataset-v3' / '10_prismacel'),
        ) if self.use_existing else ()
        for ident, label, directory in previous:
            if directory.is_dir() and under(self.root, directory):
                result[ident] = (directory, label)
        for record in sorted(self.dataset_root.glob('*/dataset.json')):
            ident = record.parent.name
            images = record.parent / 'images'
            if (not JOB_ID.fullmatch(ident) or not under(self.dataset_root, record)
                    or not images.is_dir() or not under(record.parent, images)):
                continue
            try:
                data = json.loads(record.read_text(encoding='utf-8'))
                result[ident] = (images, data['name'])
            except (OSError, ValueError, KeyError):
                continue
        return result

    def catalog(self):
        trainer = self.trainer()
        return {
            'online': {'ready': bool(os.environ.get('OPENAI_API_KEY', '').strip()), 'models': ONLINE_MODELS},
            'local': {'supported': self.windows, 'engine': str(self.engine() or ''), 'model': str(self.base() or ''),
                      'trainer': str(trainer[0]) if trainer else '', 'workspace': str(self.home),
                      'reference': str(self.reference), 'training_device': 'NVIDIA CUDA 12.4',
                      'managed_model': BASE_NAME, 'trainer_revision': TRAINER_REV,
                      'trainer_python': TRAIN_PYTHON, 'trainer_lock_sha256': sha256_file(TRAIN_LOCK)},
            'loras': [dict(id=key, label=label, weight=weight) for key, (_, label, weight) in self.loras().items()],
            'sources': [dict(id=ident, label=label, saved=bool(JOB_ID.fullmatch(ident)))
                        for ident, (_, label) in self.sources().items()],
            'jobs': self.jobs.recent(),
        }

    def list_source(self, ident):
        sources = self.sources()
        if ident not in sources:
            raise FileNotFoundError('数据集不存在。')
        root, label = sources[ident]
        items = []
        for path in sorted(root.iterdir()):
            if not path.is_file() or path.suffix.lower() not in IMAGE_TYPES or not under(root, path):
                continue
            try:
                filename(path.name)
            except ValueError:
                continue
            txt = path.with_suffix('.txt')
            caption = (txt.read_text(encoding='utf-8-sig', errors='replace').strip()[:1500]
                       if txt.is_file() and under(root, txt) and txt.stat().st_size <= 8192 else '')
            items.append({'name': path.name, 'caption': caption,
                          'url': f'/api/images/source/{ident}/{urllib.parse.quote(path.name)}'})
            if len(items) == 150:
                break
        result = {'label': label, 'items': items}
        if JOB_ID.fullmatch(ident):
            result['manifest'] = self.dataset_manifest(ident)
            result['download'] = f'/api/images/dataset/{ident}.zip'
        return result

    def source_image(self, ident, name):
        sources = self.sources()
        if ident not in sources or Path(name).suffix.lower() not in IMAGE_TYPES:
            raise FileNotFoundError('图片不存在。')
        return child_file(sources[ident][0], name)

    def save_dataset(self, data):
        name = string(data.get('name'), '数据集名称', 64)
        trigger = string(data.get('trigger', ''), '触发词', 64, required=False)
        if trigger and not re.fullmatch(r'[A-Za-z0-9_-]+', trigger):
            raise ValueError('触发词请使用英文字母、数字、下划线或短横线。')
        source = string(data.get('source', ''), '来源', 80, required=False)
        rows = data.get('items')
        if not isinstance(rows, list) or not 1 <= len(rows) <= 150:
            raise ValueError('请为数据集选择 1–150 张图。')
        self.dataset_root.mkdir(parents=True, exist_ok=True)
        ident = secrets.token_hex(8)
        total = 0
        seen = set()
        with tempfile.TemporaryDirectory(dir=self.dataset_root, prefix='dataset-') as temp:
            staged = Path(temp) / ident
            directory = staged / 'images'
            directory.mkdir(parents=True)
            items = []
            for index, row in enumerate(rows, 1):
                if not isinstance(row, dict):
                    raise ValueError('数据集条目无效。')
                original = filename(row.get('name'))
                caption = ' '.join(string(row.get('caption', ''), '图片标注', 1500, required=False).split())
                if trigger and not any(tag.strip().lower() == trigger.lower() for tag in caption.split(',')):
                    caption = f'{trigger}, {caption}' if caption else trigger
                if not caption:
                    raise ValueError(f'请给「{original}」填写标注，或填写共用触发词。')
                suffix = Path(original).suffix.lower()
                key = ('data' in row, original.casefold())
                if key in seen or suffix not in IMAGE_TYPES:
                    raise ValueError(f'重复的图片名或不支持的格式：{original}。')
                seen.add(key)
                output = directory / f'{index:03d}-{Path(original).stem[:80].rstrip(" ._-") or "image"}{suffix}'
                if 'data' in row:
                    blob = image_bytes(decode_blob(row['data']), suffix)
                    total += len(blob)
                    if total > MAX_UPLOAD:
                        raise ValueError('新上传的图片总计超过 45 MiB。')
                    output.write_bytes(blob)
                else:
                    path = self.source_image(source, original)
                    with path.open('rb') as file:
                        image_bytes(file.read(16), suffix)
                    shutil.copyfile(path, output)
                output.with_suffix('.txt').write_text(caption + '\n', encoding='utf-8')
                items.append({'name': output.name, 'original': original, 'caption': caption,
                              'sha256': sha256_file(output)})
            write_json(staged / 'dataset.json', dict(id=ident, name=name, trigger=trigger, source=source, items=items))
            staged.rename(self.dataset_root / ident)
        return {'id': ident, 'name': name, 'count': len(items)}

    def dataset_manifest(self, ident, *, verify=False):
        if not isinstance(ident, str) or not JOB_ID.fullmatch(ident) or ident not in self.sources():
            raise FileNotFoundError('已保存的数据集不存在。')
        folder = self.dataset_root / ident
        manifest = json.loads(child_file(folder, 'dataset.json').read_text(encoding='utf-8'))
        if verify:
            images = folder / 'images'
            expected = set()
            for item in manifest['items']:
                path = child_file(images, item['name'])
                caption = child_file(images, path.with_suffix('.txt').name)
                expected.update((path.name, caption.name))
                digest = sha256_file(path)
                if (item.get('sha256', digest) != digest
                        or caption.read_text(encoding='utf-8').strip() != item['caption']):
                    raise ValueError(f'数据集文件与保存时不一致：{path.name}。请另存数据集。')
                item['sha256'] = digest
            if {path.name for path in images.iterdir()} != expected:
                raise ValueError('数据集目录与清单不一致。请另存数据集。')
        return manifest

    def dataset_archive(self, ident, archive):
        manifest = self.dataset_manifest(ident, verify=True)
        folder = self.dataset_root / ident / 'images'
        with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_STORED) as zipped:
            zipped.writestr('dataset.json', json.dumps(manifest, ensure_ascii=False, indent=2))
            for item in manifest['items']:
                for name in (item['name'], Path(item['name']).with_suffix('.txt').name):
                    zipped.write(child_file(folder, name), f'images/{name}')

    def restore_dataset(self, data):
        """Restore an exported dataset with the original manifest and filenames."""
        import io

        self.dataset_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=self.dataset_root, prefix='restore-') as temp:
            stage = Path(temp)
            source = self.reference
            if 'data' in data:
                source = stage / 'unpacked'
                try:
                    with zipfile.ZipFile(io.BytesIO(decode_blob(data['data']))) as archive:
                        members = archive.infolist()
                        if len(members) > 302 or sum(item.file_size for item in members) > 512 * 1024 * 1024:
                            raise ValueError('数据集压缩包超过 150 张图或解压大小限制。')
                        for entry in members:
                            if entry.is_dir():
                                continue
                            parts = entry.filename.split('/')
                            if ((parts != ['dataset.json'] and (len(parts) != 2 or parts[0] != 'images')) or entry.flag_bits & 1
                                    or stat.S_ISLNK(entry.external_attr >> 16)):
                                raise ValueError('数据集 ZIP 只能包含 dataset.json 和 images 下的图片与标注。')
                            path = source.joinpath(*(filename(part) for part in parts))
                            path.parent.mkdir(parents=True, exist_ok=True)
                            with archive.open(entry) as read, path.open('xb') as write:
                                shutil.copyfileobj(read, write)
                except zipfile.BadZipFile as error:
                    raise ValueError('无法读取数据集 ZIP。请使用 Prisma 导出的 ZIP。') from error
            manifest_path = child_file(source, 'dataset.json')
            if manifest_path.stat().st_size > 1024 * 1024:
                raise ValueError('数据集清单超过 1 MiB。')
            manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
            if not isinstance(manifest, dict):
                raise ValueError('数据集清单无效。')
            ident = manifest.get('id')
            rows = manifest.get('items')
            if not isinstance(ident, str) or not JOB_ID.fullmatch(ident) or not isinstance(rows, list) or not 1 <= len(rows) <= 150:
                raise ValueError('数据集清单的 ID 或条目无效。')
            name = string(manifest.get('name'), '数据集名称', 64)
            image_dir = source / 'images'
            if not under(source, image_dir):
                raise ValueError('图片目录超出数据集范围。')
            output = stage / ident
            (output / 'images').mkdir(parents=True)
            saved = []
            for item in rows:
                if not isinstance(item, dict):
                    raise ValueError('数据集清单条目无效。')
                path = child_file(image_dir, item.get('name'))
                caption = child_file(image_dir, path.with_suffix('.txt').name)
                digest = sha256_file(path)
                with path.open('rb') as stream:
                    image_bytes(stream.read(16), path.suffix.lower())
                expected_caption = string(item.get('caption'), '图片标注', 1566)
                if (digest != item.get('sha256') or caption.stat().st_size > 8192
                        or caption.read_text(encoding='utf-8').strip() != expected_caption):
                    raise ValueError(f'数据集图片或标注校验失败：{path.name}。')
                for entry in (path, caption):
                    destination = output / 'images' / entry.name
                    with entry.open('rb') as read, destination.open('xb') as write:
                        shutil.copyfileobj(read, write)
                saved.append({'name': path.name, 'original': filename(item.get('original', path.name)),
                              'caption': expected_caption, 'sha256': digest})
            restored = {'id': ident, 'name': name, 'trigger': string(manifest.get('trigger', ''), '触发词', 64, required=False),
                        'source': string(manifest.get('source', ''), '来源', 80, required=False), 'items': saved}
            if (self.dataset_root / ident).exists():
                if self.dataset_manifest(ident, verify=True) != restored:
                    raise ValueError('已有不同内容的数据集使用同一个 ID。')
            else:
                write_json(output / 'dataset.json', restored)
                output.rename(self.dataset_root / ident)
        return {'id': ident, 'name': name, 'count': len(saved)}

    def payload(self, job, url, target, *, sha256, limit, label, cache_name=None):
        cached = self.downloads / (cache_name or target.name)
        from_cache = cached.is_file() and under(self.downloads, cached)
        source = cached.as_uri() if from_cache else url
        if from_cache:
            label = f'校验缓存 · {cached.name}'
        fetch_file(job, source, target, sha256=sha256, limit=limit, label=label)

    def import_lora(self, data):
        name = filename(data.get('name'))
        path = Path(name)
        if path.suffix.lower() != '.safetensors' or not LORA_STEM.fullmatch(path.stem):
            raise ValueError('请选择名称有效的 .safetensors LoRA 文件。')
        blob = decode_blob(data.get('data'))
        size = int.from_bytes(blob[:8], 'little')
        try:
            header = json.loads(blob[8:8 + size]) if 0 < size <= min(len(blob) - 8, 4 * 1024 * 1024) else {}
        except (ValueError, UnicodeError):
            header = {}
        if not isinstance(header, dict) or not any(re.search(r'(^lora_|\.lora[_.])', key) for key in header):
            raise ValueError('该文件没有可识别的 LoRA 权重。')
        self.lora_root.mkdir(parents=True, exist_ok=True)
        try:
            with (self.lora_root / f'{path.stem}.safetensors').open('xb') as stream:
                stream.write(blob)
        except FileExistsError as error:
            raise ValueError('已存在同名 LoRA，请重命名后导入。') from error
        return {'id': f'import:{path.stem}'}

    def provision(self, data):
        if not self.windows:
            raise ValueError('自动安装支持 Windows x64。')
        target = choice(data.get('target'), ('inference', 'training'), '安装范围')

        def work(job):
            self.tools.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(dir=self.tools, prefix='install-') as temp:
                stage = Path(temp)
                if not self.engine():
                    archive = self.legacy / SD_ARCHIVE
                    if self.use_existing and archive.is_file():
                        job.phase('校验已有推理工具压缩包')
                        with archive.open('rb') as stream:
                            if hashlib.file_digest(stream, 'sha256').hexdigest() != SD_HASH:
                                raise ValueError('已有推理工具压缩包 SHA-256 不符。')
                    else:
                        archive = stage / SD_ARCHIVE
                        self.payload(job, SD_URL, archive, sha256=SD_HASH, limit=48_000_000, label='下载 Vulkan 推理工具')
                    job.phase('解压 Vulkan 推理工具')
                    unpack(job, archive, stage / 'sd-cpp')
                    if not (stage / 'sd-cpp' / 'sd-cli.exe').is_file():
                        raise ValueError('工具压缩包没有 sd-cli.exe。')
                    (stage / 'sd-cpp').rename(self.tools / 'sd-cpp')
                if not self.base():
                    self.payload(job, BASE_URL, self.models / BASE_NAME, sha256=BASE_HASH, limit=2_150_000_000,
                                 label='下载 Anything V3 底模（2.13 GB）')
                if target == 'training' and not self.trainer():
                    self.install_trainer(job, stage)
            job.update(installation=self.catalog()['local'])

        return self.jobs.start('install', {'target': target}, work)

    def install_trainer(self, job, stage):
        uv = first_file(self.tools / 'uv.exe')
        if not uv:
            available = shutil.which('uv')
            if available:
                try:
                    version = subprocess.run([available, '--version'], capture_output=True, timeout=5,
                                             check=True, creationflags=subprocess.CREATE_NO_WINDOW)
                    if version.stdout.split()[0:2] == [b'uv', UV_VERSION.encode()]:
                        uv = Path(available)
                except (OSError, subprocess.SubprocessError):
                    pass
        if not uv:
            archive = stage / UV_ARCHIVE
            self.payload(job, UV_URL, archive, sha256=UV_HASH, limit=36_000_000, label='下载 Python 环境工具 uv')
            unpack(job, archive, stage / 'uv', only={'uv.exe'})
            uv = self.tools / 'uv.exe'
            (stage / 'uv' / 'uv.exe').rename(uv)

        trainer = self.tools / 'sd-scripts'
        if not (trainer / 'train_network.py').is_file():
            archive = stage / f'sd-scripts-{TRAINER_REV}.zip'
            self.payload(job, TRAINER_URL, archive, sha256=TRAINER_HASH, limit=32_000_000, label='下载固定版本的 sd-scripts')
            code = stage / 'sd-scripts'
            unpack(job, archive, code, strip_root=f'sd-scripts-{TRAINER_REV}')
            code.rename(trainer)

        for name, digest in TOKENIZER_FILES.items():
            path = self.tools / 'tokenizer-cache' / TOKENIZER / name
            if not path.is_file() or sha256_file(path) != digest:
                self.payload(job, f'{CLIP_URL}/{name}', path, sha256=digest, limit=5_000_000,
                             label=f'下载 CLIP 分词文件 · {name}', cache_name=f'clip-{name}')

        env = {'UV_CACHE_DIR': os.environ.get('PRISMA_UV_CACHE', str(self.tools / 'uv-cache')),
               'UV_PYTHON_INSTALL_DIR': str(self.tools / 'python'), 'UV_NO_MODIFY_PATH': '1',
               'UV_NO_PROGRESS': '1', 'UV_LINK_MODE': 'hardlink'}
        python = self.tools / 'venv' / 'Scripts' / 'python.exe'
        job.phase(f'安装独立 Python {TRAIN_PYTHON} 与虚拟环境')
        # Keep a usable environment on retries; install the exact interpreter
        # when upgrading an environment created by an older installer.
        version = self.tools / 'venv' / 'pyvenv.cfg'
        if not python.is_file() or not version.is_file() or not re.search(
            rf'^version(?:_info)?\s*=\s*{re.escape(TRAIN_PYTHON)}(?:\s|$)', version.read_text(), re.MULTILINE
        ):
            job.run([uv, 'venv', '--clear', '--managed-python', '--python', TRAIN_PYTHON, self.tools / 'venv'], env=env)
        job.phase('安装 CUDA 版 PyTorch 2.6（首次下载较大）')
        job.run([uv, 'pip', 'install', '--no-deps', '--python', python, '--index-url', 'https://download.pytorch.org/whl/cu124',
                 'torch==2.6.0+cu124', 'torchvision==0.21.0+cu124'], env=env)
        job.phase('按版本锁安装 LoRA 训练依赖')
        job.run([uv, 'pip', 'install', '--no-deps', '--python', python, '--index-url', 'https://pypi.org/simple',
                 '--requirements', TRAIN_LOCK], env=env, cwd=trainer)
        job.run([uv, 'pip', 'check', '--python', python], env=env)
        job.phase('检查训练依赖和 CUDA')
        self.check_cuda(job, python, trainer)
        write_json(self.tools / 'trainer-ready.json', {'revision': TRAINER_REV, 'torch': '2.6.0+cu124',
                   'python': TRAIN_PYTHON, 'lock_sha256': sha256_file(TRAIN_LOCK)})

    def identity(self, path):
        current = path.stat()
        key = (current.st_size, current.st_mtime_ns)
        cached = self.identities.get(path)
        if cached and cached[0] == key:
            return cached[1]
        result = {'name': path.name, 'size': current.st_size, 'sha256': sha256_file(path)}
        self.identities[path] = (key, result)
        return result

    def replay(self, ident):
        original = self.jobs.get(ident)
        options = original['options'].copy()
        if original['kind'] == 'generate':
            if options['mode'] == 'local':
                variants = options['loras']
                options['loras'] = [item['id'] for item in variants]
                options['weight'] = variants[0]['weight'] or 0.35
            return self.generate(options, original=original)
        if original['kind'] == 'train':
            return self.train(options, original=original)
        if original['kind'] == 'install':
            return self.provision(options)
        raise ValueError('此任务类型不支持复跑。')

    @staticmethod
    def record_inputs(job, inputs, original):
        if original and original.get('inputs') and inputs != original['inputs']:
            raise ValueError('当前输入文件或工具版本与原任务记录不一致。请恢复原文件，或提交新任务。')
        job.update(inputs=inputs)

    @staticmethod
    def check_cuda(job, python, code):
        try:
            job.run([python, '-c', 'import torch, bitsandbytes, accelerate, diffusers, transformers, networks.lora; '
                     'assert torch.cuda.is_available(), "NVIDIA CUDA unavailable"; '
                     'print(torch.__version__, torch.cuda.get_device_name(0))'], cwd=code)
        except ValueError as error:
            raise ValueError('训练环境检查失败；请检查 NVIDIA 驱动、CUDA 和任务日志。') from error

    def generate(self, data, *, original=None):
        mode = choice(data.get('mode'), ('online', 'local'), '生成方式')
        prompt = string(data.get('prompt'), '提示词', 4000)
        count = number(data.get('count', 1), '生成张数', 1, 4, integer=True)
        options = dict(mode=mode, prompt=prompt, count=count)
        if original:
            options['replay_of'] = original['id']
        if mode == 'online':
            key = os.environ.get('OPENAI_API_KEY', '').strip()
            if not key:
                raise ValueError('请在启动 WebUI 前设置 OPENAI_API_KEY，然后重启服务。')
            options.update(model=choice(data.get('model', ONLINE_MODELS[0]), ONLINE_MODELS, '在线模型'),
                           size=choice(data.get('size', ONLINE_SIZES[0]), ONLINE_SIZES, '图片尺寸'),
                           quality=choice(data.get('quality', 'medium'), ('low', 'medium', 'high'), '质量'))
            return self.jobs.start('generate', options, lambda job: self.online_generate(job, options, key))

        model, engine = self.base(), self.engine()
        if not self.windows or not model or not engine:
            raise ValueError('请先在本地工具箱安装推理工具和底模。')
        negative = string(data.get('negative', ''), '负面提示词', 4000, required=False)
        if re.search(r'<\s*lora\s*[:>]', prompt + '\n' + negative, re.IGNORECASE):
            raise ValueError('请选择表单中的 LoRA；提示词无需填写 LoRA 标签。')
        width = number(data.get('width', 512), '宽度', 256, 1024, integer=True)
        height = number(data.get('height', 896), '高度', 256, 1024, integer=True)
        if width % 64 or height % 64 or width * height > 640 * 1024:
            raise ValueError('宽高须为 64 的倍数，且总像素不超过 655,360。')
        first_seed = data.get('seed')
        seed = secrets.randbelow(2_147_483_647 - count) if first_seed is None else number(
            first_seed, 'Seed', 0, 2_147_483_647 - count + 1, integer=True)
        ids = data.get('loras', [''])
        found = self.loras()
        if (not isinstance(ids, list) or not 1 <= len(ids) <= 2
                or any(not isinstance(ident, str) or (ident and ident not in found) for ident in ids)
                or len(set(ids)) != len(ids)):
            raise ValueError('请选择一个 LoRA、底模，或两项不同的 LoRA 对照。')
        weight = number(data.get('weight', 0.35), 'LoRA 权重', 0.05, 1.5)
        if len(ids) > 1 and count > 2:
            raise ValueError('对照模式每个分支最多生成两张。')
        variants = []
        for ident in ids:
            path, label, default = found.get(ident, (None, '底模', None))
            variants.append((path, ident, label, (default if len(ids) > 1 else weight) if path else None))
        options.update(model=BASE_NAME, negative=negative, width=width, height=height, seed=seed,
                       steps=number(data.get('steps', 20), '采样步数', 1, 60, integer=True),
                       cfg=number(data.get('cfg', 6.5), 'CFG', 1, 15),
                       sampler=choice(data.get('sampler', 'euler_a'), SAMPLERS, '采样器'), clip_skip=2,
                       loras=[dict(id=ident, label=label, weight=strength) for _, ident, label, strength in variants])
        def work(job):
            job.phase('核对模型和 LoRA 文件')
            inputs = {'base': self.identity(model), 'engine': self.identity(engine),
                      'engine_library': self.identity(engine.with_name('stable-diffusion.dll'))
                      if engine.with_name('stable-diffusion.dll').is_file() else None,
                      'loras': [self.identity(path) if path else None for path, _, _, _ in variants],
                      'weights': [weight for _, _, _, weight in variants], 'vae_tile_size': '256x448'}
            self.record_inputs(job, inputs, original)
            self.local_generate(job, options, model, engine, variants)

        return self.jobs.start('generate', options, work)

    @staticmethod
    def online_generate(job, options, key):
        job.phase('等待 OpenAI 生成图片')
        payload = dict(model=options['model'], prompt=options['prompt'], n=options['count'],
                       size=options['size'], quality=options['quality'], output_format='png')
        req = urllib.request.Request('https://api.openai.com/v1/images/generations',
                                     data=json.dumps(payload).encode(),
                                     headers={'Authorization': f'Bearer {key}', 'Content-Type': 'application/json'})
        try:
            with urllib.request.build_opener(NoRedirect()).open(req, timeout=300) as response:
                limit = 96 * 1024 * 1024
                raw = response.read(limit + 1)
                if len(raw) > limit:
                    raise ValueError('在线服务返回的图片过大。')
                content = json.loads(raw)['data']
        except urllib.error.HTTPError as error:
            reasons = {400: '请求或模型参数未获接受', 401: 'API 密钥不可用', 403: '账号无模型权限',
                       429: '账户额度或请求频率受限'}
            raise ValueError(f'OpenAI 请求失败（HTTP {error.code}）：{reasons.get(error.code, "请稍后重试")}。') from None
        except (urllib.error.URLError, TimeoutError):
            raise ValueError('无法连接 OpenAI，或等待图片超时。') from None
        except (KeyError, TypeError, json.JSONDecodeError, UnicodeError):
            raise ValueError('在线服务返回了无法读取的图片数据。') from None
        job.check()
        if not isinstance(content, list) or not 1 <= len(content) <= options['count']:
            raise ValueError('在线服务没有返回预期的图片。')
        directory = job.directory / 'images'
        directory.mkdir()
        images = []
        for i, item in enumerate(content, 1):
            job.check()
            blob = image_bytes(decode_blob(item.get('b64_json') if isinstance(item, dict) else None), '.png')
            name = f'online-{i:02d}.png'
            (directory / name).write_bytes(blob)
            images.append(dict(name=name, label=f'{options["model"]} · {i}', seed=None,
                               sha256=hashlib.sha256(blob).hexdigest()))
        if len(images) < options['count']:
            job.update(note=f'在线服务只返回了 {len(images)} 张图片。')
        job.update(images=images)

    @staticmethod
    def local_generate(job, options, model, engine, variants):
        directory = job.directory / 'images'
        directory.mkdir()
        images = []
        for index, (path, _, label, weight) in enumerate(variants, 1):
            job.phase(f'本地生成 · {label}' + (f' / {weight:g}' if path else ''))
            prompt = options['prompt']
            args = [engine, '-m', model]
            if path:
                args += ['--lora-model-dir', path.parent]
                prompt += f' <lora:{path.stem}:{weight:g}>'
            args += ['-p', prompt, '-n', options['negative'], '-o', directory / f'local-{index}-%03d.png',
                     '-W', options['width'], '-H', options['height'], '--steps', options['steps'],
                     '--cfg-scale', options['cfg'], '--clip-skip', options['clip_skip'],
                     '--sampling-method', options['sampler'], '-s', options['seed'], '-b', options['count'],
                     '--vae-tiling', '--vae-tile-size', '256x448']
            offset = job.log.stat().st_size if job.log.exists() else 0
            job.run(args, cwd=job.directory)
            if path:
                with job.log.open('rb') as stream:
                    stream.seek(offset)
                    log = stream.read().decode('utf-8', errors='replace')
                if not any(0 < int(m[1]) == int(m[2]) for m in re.finditer(r'\((\d+) / (\d+)\) LoRA tensors have been applied', log)):
                    raise ValueError(f'{label} 的 LoRA 权重未完整应用。请查看日志并检查权重是否兼容 SD 1.x。')
            for n in range(options['count']):
                name = f'local-{index}-{n:03d}.png'
                with child_file(directory, name).open('rb') as stream:
                    image_bytes(stream.read(16), '.png')
                images.append(dict(name=name, label=label, weight=weight, seed=options['seed'] + n,
                                   sha256=sha256_file(directory / name)))
            job.update(images=sorted(images, key=lambda item: item['seed']))

    def train(self, data, *, original=None):
        trainer, model = self.trainer(), self.base()
        if not self.windows or not trainer or not model:
            raise ValueError('请先安装 LoRA 训练环境和底模。')
        dataset = data.get('dataset')
        sources = self.sources()
        if not isinstance(dataset, str) or not JOB_ID.fullmatch(dataset) or dataset not in sources:
            raise ValueError('请先保存一个数据集，再选中它开始训练。')
        name = data.get('name')
        if not isinstance(name, str) or not SLUG.fullmatch(name) or PureWindowsPath(name).is_reserved():
            raise ValueError('LoRA 名称须为 1–48 个英文字母、数字、下划线或短横线，并以字母或数字开头。')
        steps = number(data.get('steps', 200), '训练步数', 1, 5000, integer=True)
        options = dict(dataset=dataset, dataset_name=sources[dataset][1], name=name, steps=steps,
                       save_every=number(data.get('save_every', min(100, steps)), '保存间隔', 1, steps, integer=True),
                       rank=number(data.get('rank', 4), 'Rank', 2, 64, integer=True),
                       lr=number(data.get('lr', 0.0001), '学习率', 0.000001, 0.001),
                       repeats=number(data.get('repeats', 10), '重复次数', 1, 50, integer=True),
                       seed=number(data.get('seed', 1065270805), '训练 Seed', 0, 2_147_483_647, integer=True),
                       base_model=BASE_NAME, parent_lora=None)
        if steps // options['save_every'] > 30:
            raise ValueError('请增大保存间隔，每次训练最多保存约 30 个中间权重。')
        if original:
            options['replay_of'] = original['id']
        directory = sources[dataset][0]

        def work(job):
            job.phase('核对底模和训练数据集')
            manifest = self.dataset_manifest(dataset, verify=True)
            python, code, cache = trainer
            managed = code.parent == self.tools
            runtime_file = job.directory / 'runtime.json'
            job.run([python, '-c', 'import importlib.metadata as m, json, pathlib, sys; '
                     'packages = {d.metadata["Name"].lower(): d.version for d in m.distributions()}; '
                     'pathlib.Path(sys.argv[1]).write_text(json.dumps({"python": sys.version.split()[0], '
                     '"packages": packages}, sort_keys=True), encoding="utf-8")', runtime_file], cwd=code)
            inputs = {'base': self.identity(model), 'dataset': manifest,
                      'trainer_revision': TRAINER_REV if managed else None,
                      'trainer_lock_sha256': sha256_file(TRAIN_LOCK) if managed else None,
                      'trainer_script': self.identity(code / 'train_network.py'),
                      'loader': self.identity(code / 'library' / 'model_util.py'),
                      'runner': self.identity(Path(__file__).with_name('lora_train_runner.py')),
                      'runtime': json.loads(runtime_file.read_text(encoding='utf-8')),
                      'tokenizer': {name: sha256_file(cache / TOKENIZER / name) for name in TOKENIZER_FILES}}
            self.record_inputs(job, inputs, original)
            self.train_lora(job, options, trainer, model, directory)

        return self.jobs.start('train', options, work)

    def train_lora(self, job, options, trainer, model, images):
        python, code, cache = trainer
        job.phase('检查 CUDA 训练环境')
        self.check_cuda(job, python, code)
        dataset = job.directory / 'dataset.toml'
        dataset.write_text('[general]\ncaption_extension = ".txt"\n\n[[datasets]]\n'
                           'resolution = [512, 512]\nbatch_size = 1\nenable_bucket = true\n'
                           'min_bucket_reso = 256\nmax_bucket_reso = 1024\n\n[[datasets.subsets]]\n'
                           f'image_dir = {json.dumps(images.as_posix(), ensure_ascii=False)}\n'
                           f'num_repeats = {options["repeats"]}\n', encoding='utf-8')
        output = job.directory / 'checkpoints'
        output.mkdir()
        job.phase(f'训练 LoRA · {options["name"]}（{options["steps"]} 步）')
        command = [python, '-m', 'accelerate.commands.launch', '--num_processes', '1', '--num_machines', '1',
                 '--dynamo_backend', 'no', '--mixed_precision', 'fp16', '--num_cpu_threads_per_process', '1',
                 Path(__file__).with_name('lora_train_runner.py'), '--pretrained_model_name_or_path', model,
                 '--dataset_config', dataset, '--output_dir', output, '--output_name', options['name'],
                 '--tokenizer_cache_dir', cache, '--network_module', 'networks.lora', '--network_train_unet_only',
                 '--network_dim', options['rank'], '--network_alpha', options['rank'],
                 '--max_train_steps', options['steps'], '--save_every_n_steps', options['save_every'],
                 '--learning_rate', options['lr'], '--unet_lr', options['lr'], '--lr_scheduler', 'cosine',
                 '--optimizer_type', 'AdamW8bit', '--clip_skip', '2', '--seed', options['seed'],
                 '--save_model_as', 'safetensors', '--mixed_precision', 'fp16', '--save_precision', 'fp16',
                 '--sdpa', '--cache_latents', '--gradient_checkpointing', '--lowram', '--max_data_loader_n_workers', '0']
        job.update(command=[str(item) for item in command])
        job.run(command, cwd=code, env={'OMP_NUM_THREADS': '1', 'MKL_NUM_THREADS': '1', 'PYTHONFAULTHANDLER': '1',
                               'HF_HUB_OFFLINE': '1', 'HF_HOME': str(self.tools / 'hf-cache')})
        final = child_file(output, f'{options["name"]}.safetensors')
        checkpoints = []
        for path in sorted(output.glob('*.safetensors')):
            if not path.is_file() or not under(output, path):
                continue
            step = re.search(r'-step(\d+)$', path.stem)
            label = f'{options["name"]} · {int(step[1])} 步' if step else f'{options["name"]} · 最终权重'
            checkpoints.append(dict(name=path.name, label=label, id=f'{job.directory.name}:{path.stem}',
                                    sha256=sha256_file(path)))
        if final.stat().st_size <= 8:
            raise ValueError('训练没有生成有效的权重。')
        job.update(checkpoints=checkpoints)

    def artifact(self, ident, category, name):
        job = self.jobs.get(ident)
        if category not in ('images', 'checkpoints') or not any(item['name'] == name for item in job[category]):
            raise FileNotFoundError('产物不存在。')
        return child_file(self.jobs.directory(ident) / category, name)
