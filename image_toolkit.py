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
import sys
import tempfile
import tarfile
import threading
import urllib.error
import urllib.parse
import urllib.request
import zipfile

from image_jobs import INDEX_PROJECTION, Jobs, JOB_ID, now, write_json
from image_library import ImageLibrary, KINDS, artifact_id, timestamp
from workspace_resources import MEDIA_TYPES, WorkspaceResources, content_id
from prisma import normalize_scenario


SD_ARCHIVE = 'sd-master-b521db4-bin-win-vulkan-x64.zip'
SD_URL = f'https://github.com/leejet/stable-diffusion.cpp/releases/download/master-1098-b521db4/{SD_ARCHIVE}'
SD_HASH = '39f90af4f4d407750c06974f2a58e187feb2fa8b284436ea3a8756fcd6f34d39'
BASE_NAME = 'Anything-V3.0-pruned-fp16.safetensors'
BASE_URL = f'https://huggingface.co/admruul/anything-v3.0/resolve/3d14c2790f2f04b631bcb8f939b78fb5fde275d3/{BASE_NAME}'
BASE_HASH = 'ed376204fb1e93cde85757de92c9f737dfb53cef97e7fadb33b99f94e2423469'
SDXL_REVISION = 'a7c2bcc30a3b5489f1f1989e66cd5fe957fdb45c'
SDXL_CONFIG_REPO = 'stabilityai/stable-diffusion-xl-base-1.0'
SD1_CONFIG_REPO = 'stable-diffusion-v1-5/stable-diffusion-v1-5'
SD1_CONFIG_REVISION = '451f4fe16113bff5a5d2269ed5ad43b0592e9a14'
SDXL_REPO = 'cagliostrolab/animagine-xl-4.0'
SDXL_MODEL_REVISION = '2b7c1b397761bf5bd3cc42e5b39ec99314a75a96'
SDXL_NAME = 'animagine-xl-4.0-opt.safetensors'
SDXL_URL = f'https://huggingface.co/{SDXL_REPO}/resolve/{SDXL_MODEL_REVISION}/{SDXL_NAME}'
SDXL_HASH = '6327eca98bfb6538dd7a4edce22484a1bbc57a8cff6b11d075d40da1afb847ac'
SDXL_TOKENIZER = 'laion_CLIP-ViT-bigG-14-laion2B-39B-b160k'
SDXL_TOKENIZER_FILES = ('merges.txt', 'special_tokens_map.json', 'tokenizer_config.json', 'vocab.json')
SDXL_TOKENIZER_URL = (f'https://huggingface.co/stabilityai/stable-diffusion-xl-base-1.0/resolve/'
                      f'{SDXL_REVISION}/tokenizer_2')
TRAINING_BASES = {
    'sd1': {'label': 'Anything V3 · SD 1.x', 'name': BASE_NAME, 'sha256': BASE_HASH,
            'url': BASE_URL, 'revision': '3d14c2790f2f04b631bcb8f939b78fb5fde275d3',
            'limit': 2_150_000_000, 'resolution': 512, 'family': 'sd1'},
    'sdxl': {'label': 'Animagine XL 4.0 Opt · SDXL anime', 'name': SDXL_NAME, 'sha256': SDXL_HASH,
             'url': SDXL_URL, 'revision': SDXL_MODEL_REVISION, 'repo': SDXL_REPO,
             'limit': 7_500_000_000,
             'resolution': 1024, 'family': 'sdxl'},
    'iris3b': {'label': 'Iris-3B Base · MLX', 'name': 'iris-3b', 'revision': 'latest-at-install',
               'repo': 'speridlabs/iris-3b', 'model': 'iris-3b-base', 'resolution': 512,
               'family': 'iris'},
    'anima': {'label': 'Anima Base · MLX · 动漫专用', 'name': 'anima',
              'revision': '457fbf842cb86e96af72c65bdd13e3f1c448de84',
              'repo': 'circlestone-labs/Anima', 'model': 'anima-base',
              'resolution': 512, 'family': 'anima'},
}
UV_ARCHIVE = 'uv-x86_64-pc-windows-msvc.zip'
UV_VERSION = '0.11.19'
UV_URL = f'https://github.com/astral-sh/uv/releases/download/{UV_VERSION}/{UV_ARCHIVE}'
UV_HASH = '1665fc8e37b5d70a134820d6d7891747471a2ac8bc940ee7af0b69fd03b28d61'
TRAIN_PYTHON = '3.10.20'
TRAIN_LOCK = Path(__file__).with_name('requirements-lora-win-cu124.txt')
MPS_TRAIN_LOCK = Path(__file__).with_name('requirements-lora-macos-mps.txt')
MPS_TRAIN_PYTORCH = '2.8.0'
MPS_TRAIN_TORCHVISION = '0.23.0'
MFLUX_VERSION = '0.24.0'
MFLUX_PYTHON = '3.13'
MFLUX_REPO = 'https://github.com/mflux-community/mflux'
MFLUX_REVISION = '02b92461ec246ec44f5149b5dc3f69817a263a67'
ANIMA_REPO = 'circlestone-labs/Anima'
ANIMA_REVISION = '457fbf842cb86e96af72c65bdd13e3f1c448de84'
IRIS_TEXT_ENCODER = 'Qwen/Qwen3-VL-4B-Instruct'
IRIS_TEXT_ENCODER_REVISION = 'ebb281ec70b05090aa6165b016eac8ec08e71b17'
TRAINER_REV = 'cb70b96858477e716a2096292ab8aab433277f38'
TRAINER_URL = f'https://codeload.github.com/kohya-ss/sd-scripts/zip/{TRAINER_REV}'
TRAINER_HASH = '13587ab358c5ecb878a991a391ca924cb37ff1d427805b1476f41bbf23fcc493'
MAC_UV_ARCHIVE = 'uv-aarch64-apple-darwin.tar.gz'
MAC_UV_URL = f'https://github.com/astral-sh/uv/releases/download/{UV_VERSION}/{MAC_UV_ARCHIVE}'
MAC_UV_HASH = 'd8f59c38e8c4168ee468d423cd63184be12fa6995a4283d41ee1a14d003c9453'
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
SAMPLERS = ('euler_a', 'euler', 'dpm++2m', 'er_sde')
DIFFUSERS_SAMPLERS = ('euler_a', 'euler', 'dpm++2m')
ANIMA_SAMPLERS = ('er_sde', 'euler')
IMAGE_TYPES = {'.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.webp': 'image/webp'}
MAX_UPLOAD = 45 * 1024 * 1024
SLUG = re.compile(r'[a-zA-Z0-9][a-zA-Z0-9_-]{0,47}')
LORA_STEM = re.compile(r'[\w][\w. -]{0,127}')
IMPORTED_LORA_STRENGTHS = {'sd1': 0.35, 'sdxl': 0.65, 'anima': 1.0}


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


def windows_reserved_name(name):
    path = PureWindowsPath(name)
    check = getattr(path, 'is_reserved', None)
    if check:
        return check()
    check = getattr(os.path, 'isreserved', None)
    if check:
        return check(name)
    stem = path.name.split('.', 1)[0].rstrip(' .').upper()
    return (stem in {'CON', 'PRN', 'AUX', 'NUL'}
            or bool(re.fullmatch(r'(?:COM|LPT)[1-9¹²³]', stem)))


def filename(name):
    if (not isinstance(name, str) or not 0 < len(name) <= 160 or name != name.lstrip(' ').rstrip(' .')
            or any(c in '<>:"/\\|?*' or ord(c) < 32 for c in name) or windows_reserved_name(name)):
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


def record_model(job):
    options = job.get('options', {})
    if options.get('mode') == 'online' and options.get('model'):
        return f'openai:{options["model"]}'
    value = options.get('base_model') or options.get('model')
    return {BASE_NAME: 'sd1', 'anything-v3': 'sd1', SDXL_NAME: 'sdxl'}.get(
        value, value or ('sd1' if job.get('kind') in ('train', 'generate', 'install') else None))


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
        self.library = ImageLibrary(self.home)
        self.resources = WorkspaceResources(self.library, self.artifact)
        self.jobs = Jobs(self.home / 'jobs', self.library, self.index_job)
        self._library_lock = threading.RLock()
        self._library_ready = False
        self.identities = {}
        self.windows = os.name == 'nt' and platform.machine().lower() in ('amd64', 'x86_64')
        self.apple_silicon = sys.platform == 'darwin' and platform.machine().lower() in ('arm64', 'aarch64')
        self.training_backend = 'cuda' if self.windows else 'mps' if self.apple_silicon else None

    @property
    def training_supported(self):
        return self.training_backend is not None

    @property
    def training_lock(self):
        return MPS_TRAIN_LOCK if self.training_backend == 'mps' else TRAIN_LOCK

    def training_python(self, parent):
        executable = 'python.exe' if self.windows else 'python'
        directory = 'Scripts' if self.windows else 'bin'
        return parent / 'venv' / directory / executable

    def engine(self):
        installed = self.tools / 'sd-cpp' / 'sd-cli.exe'
        return first_file(installed, self.legacy / 'stable-diffusion.cpp' / 'sd-cli.exe') if self.use_existing else first_file(installed)

    def base(self):
        installed = self.models / BASE_NAME
        return first_file(installed, self.legacy / BASE_NAME) if self.use_existing else first_file(installed)

    def training_base(self, model_id):
        if model_id == 'sd1':
            return self.base()
        profile = TRAINING_BASES[model_id]
        if model_id == 'iris3b':
            installed = self.models / profile['name']
            return installed if (installed / 'model.safetensors').is_file() and (installed / 'config.yaml').is_file() else None
        if model_id == 'anima':
            installed = self.models / profile['name']
            required = (installed / 'split_files' / 'diffusion_models' / 'anima-base-v1.0.safetensors',
                        installed / 'split_files' / 'text_encoders' / 'qwen_3_06b_base.safetensors',
                        installed / 'split_files' / 'vae' / 'qwen_image_vae.safetensors',
                        installed / 'tokenizer' / 'tokenizer.json',
                        installed / 'tokenizer_2' / 'spiece.model')
            return installed if all(path.is_file() for path in required) else None
        return first_file(self.models / profile['name'])

    def training_models(self):
        sd_trainer = bool(self.trainer())
        models = [dict(id=ident, label=profile['label'], name=profile['name'],
                       resolution=profile['resolution'], available=bool(self.training_base(ident)),
                       trainer_available=sd_trainer)
                  for ident, profile in TRAINING_BASES.items() if ident not in ('iris3b', 'anima')]
        if self.apple_silicon:
            models.append(dict(id='iris3b', label=TRAINING_BASES['iris3b']['label'], name='iris-3b-base',
                               resolution=512, available=bool(self.training_base('iris3b')),
                               trainer_available=bool(self.mflux_environment())))
            models.append(dict(id='anima', label=TRAINING_BASES['anima']['label'], name='anima-base',
                               resolution=512, available=bool(self.training_base('anima')),
                               trainer_available=bool(self.mflux_environment())))
        return models

    def mflux_environment(self):
        parent = self.tools / 'mflux'
        python = parent / 'venv' / 'bin' / 'python'
        cli = parent / 'venv' / 'bin' / 'mflux-train'
        marker = parent / 'mflux-ready.json'
        try:
            ready = json.loads(marker.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            ready = {}
        if (self.apple_silicon and ready.get('mflux') == MFLUX_VERSION
                and ready.get('mflux_revision') == MFLUX_REVISION and python.is_file()
                and cli.is_file() and (parent / 'venv' / 'bin' / 'mflux-generate-iris').is_file()
                and (parent / 'venv' / 'bin' / 'mflux-generate-anima').is_file()):
            return python, cli, parent / 'venv', parent
        return None

    def model_revision_record(self, model_id):
        if model_id in ('iris3b', 'anima'):
            path = self.models / f'{TRAINING_BASES[model_id]["name"]}.prisma.json'
            try:
                data = json.loads(path.read_text(encoding='utf-8'))
                return data if data.get('model_repo') == TRAINING_BASES[model_id]['repo'] else None
            except (OSError, ValueError):
                return None
        return {'revision': TRAINING_BASES[model_id]['revision']}

    @staticmethod
    def tokenizer_hashes(cache):
        result = {TOKENIZER: {name: sha256_file(cache / TOKENIZER / name) for name in TOKENIZER_FILES
                              if (cache / TOKENIZER / name).is_file()}}
        second = cache / SDXL_TOKENIZER
        result[SDXL_TOKENIZER] = {name: sha256_file(second / name) for name in SDXL_TOKENIZER_FILES
                                  if (second / name).is_file()}
        return result

    def trainer(self):
        parents = (self.tools, self.pilot) if self.use_existing and self.windows else (self.tools,)
        for parent in parents:
            code = parent / 'sd-scripts'
            python = self.training_python(parent)
            cache = parent / 'tokenizer-cache'
            ready = parent == self.pilot
            if parent == self.tools:
                try:
                    marker = json.loads((parent / 'trainer-ready.json').read_text(encoding='utf-8'))
                    expected_torch = ('2.6.0+cu124' if self.training_backend == 'cuda'
                                      else MPS_TRAIN_PYTORCH)
                    expected_torchvision = ('0.21.0+cu124' if self.training_backend == 'cuda'
                                            else MPS_TRAIN_TORCHVISION)
                    ready = (marker.get('revision') == TRAINER_REV
                             and marker.get('lock_sha256') == sha256_file(self.training_lock)
                             and marker.get('python') == TRAIN_PYTHON
                             and marker.get('backend') == self.training_backend
                             and marker.get('torch') == expected_torch
                             and marker.get('torchvision') == expected_torchvision
                             and marker.get('sdxl_model_revision') == SDXL_REVISION
                             and marker.get('sdxl_tokenizer_hashes') ==
                             self.tokenizer_hashes(cache).get(SDXL_TOKENIZER))
                except (OSError, ValueError, AttributeError):
                    pass
            if (ready and python.is_file() and (code / 'train_network.py').is_file()
                    and (code / 'sdxl_train_network.py').is_file()
                    and all((cache / TOKENIZER / name).is_file()
                            and sha256_file(cache / TOKENIZER / name) == digest
                            for name, digest in TOKENIZER_FILES.items())
                    and all((cache / SDXL_TOKENIZER / name).is_file()
                            for name in SDXL_TOKENIZER_FILES)):
                return python, code, cache
        return None

    def standalone_loras(self):
        found = {}
        previous = (('output', 'R1'), ('inference-lora', 'R1'),
                    ('output-v2', 'R2'), ('output-v3', 'R3')) if self.use_existing else ()
        for directory, run in previous:
            for path in sorted((self.pilot / directory).glob('*.safetensors')):
                if not path.is_file() or not under(self.pilot / directory, path) or not LORA_STEM.fullmatch(path.stem):
                    continue
                step = re.search(r'-step(\d+)$', path.stem)
                label = f'{run} · {int(step[1])} 步' if step else f'{run} · 最终权重'
                key = f'{run.lower()}-{int(step[1])}' if step else f'{run.lower()}-final'
                found[key] = (path, label, 0.45 if run == 'R1' else 0.35, 'sd1')
        directories = [('import:', self.lora_root)]
        directories.extend((f'import:{path.name}:', path)
                           for path in sorted((self.lora_root / 'projects').glob('*'))
                           if JOB_ID.fullmatch(path.name) and path.is_dir() and under(self.lora_root, path))
        for prefix, root in directories:
            for family, strength in IMPORTED_LORA_STRENGTHS.items():
                directory = root if family == 'sd1' else root / family
                family_prefix = '' if family == 'sd1' else f'{family}:'
                for path in sorted(directory.glob('*.safetensors')):
                    if path.is_file() and under(self.lora_root, path) and LORA_STEM.fullmatch(path.stem):
                        found[f'{prefix}{family_prefix}{path.stem}'] = (path, path.stem, strength, family)
        return found

    def loras(self):
        self.ensure_library()
        found = self.standalone_loras()
        for record in self.library.trained_loras():
            try:
                job_id = record['id'].split(':', 1)[0]
                path = child_file(self.jobs.directory(job_id) / 'checkpoints', Path(record['path']).name)
            except (OSError, ValueError):
                continue
            metadata = record['metadata']
            found[record['id']] = (path, record['label'], metadata['strength'], metadata['family'])
        return found

    def index_job(self, directory, job):
        ident, job_kind = job['id'], job['kind']
        manifest = directory / 'job.json'
        stat = manifest.stat()
        updated = timestamp(manifest)
        options = job.get('options', {})
        project = options.get('project_id')
        model = record_model(job)
        family = TRAINING_BASES.get(model, {}).get('family', 'sd1')
        links = []
        if model:
            links.append(('uses_model', 'model', model))
        if options.get('dataset'):
            links.append(('uses_dataset', 'dataset', options['dataset']))
        for variant in options.get('loras', []):
            lora = variant.get('id') if isinstance(variant, dict) else variant
            if lora:
                links.append(('uses_lora', 'lora', lora))
        parent = options.get('parent_lora')
        if parent:
            links.append(('continues_from', 'lora', parent.get('id') if isinstance(parent, dict) else parent))
        if options.get('replay_of'):
            links.append(('replay_of', 'job', options['replay_of']))
        if job_kind == 'generate' and options.get('prompt'):
            prompt = options.get('prompt_id') or self.resources.prompt(
                options['prompt'], options.get('negative') or '', project=project)
            links.append(('uses_prompt', 'prompt', prompt))
        if options.get('project_id'):
            links.append(('for_project', 'project', options['project_id']))
        inputs = job.get('inputs') or {}
        if job_kind == 'train':
            links.append(('uses_tool', 'tool', 'mflux' if family in ('anima', 'iris') else 'sd-scripts'))
        elif inputs.get('engine'):
            links.append(('uses_tool', 'tool', 'sd-cpp'))
        title = options.get('name') or options.get('prompt', '')[:100] or str(options.get('target') or job_kind)
        summary = {key: job[key] for key in ('started', 'ended', 'phase', 'error', 'options') if key in job}
        summary.update(job_kind=job_kind, stamp=[stat.st_mtime_ns, stat.st_size, INDEX_PROJECTION])
        records = [dict(kind='job', id=ident, label=title, path=manifest, state=job['state'], project_id=project,
                        updated=updated, metadata=summary, links=links)]
        for category in ('images', 'checkpoints'):
            for item in job.get(category, []):
                try:
                    name = filename(item['name'])
                except (KeyError, ValueError, TypeError):
                    continue
                path = directory / category / name
                label = item.get('label') or name
                file_id = artifact_id(ident, category, name)
                origin = ('produced_by', 'job', ident)
                metadata = dict(item, category=category, job_id=ident,
                                url=f'/api/images/files/{ident}/{category}/{urllib.parse.quote(name)}')
                records.append(dict(kind='artifact', id=file_id, label=label, path=path, project_id=project,
                                    updated=updated, metadata=metadata, links=[origin]))
                if job_kind == 'train' and job['state'] == 'done' and category == 'checkpoints' and path.suffix == '.safetensors':
                    lora_id = f'{ident}:{path.stem}'
                    records.append(dict(kind='lora', id=lora_id, label=label, path=path, updated=updated, project_id=project,
                                        metadata=dict(family=family, strength=1.0 if family == 'anima' else 0.35,
                                                      sha256=item.get('sha256')),
                                        links=[origin, ('weight_file', 'artifact', file_id), *links]))
        self.library.replace(f'job:{ident}', records)

    def index_dataset(self, manifest):
        ident = manifest['id']
        path = self.dataset_root / ident / 'dataset.json'
        source = manifest.get('source', '')
        links = [('derived_from', 'dataset', source)] if JOB_ID.fullmatch(source or '') else []
        metadata = dict(count=len(manifest['items']), trigger=manifest.get('trigger'), source=source,
                        url=f'/api/images/dataset/{ident}.zip')
        updated = timestamp(path)
        project = manifest.get('project_id')
        records = [dict(kind='dataset', id=ident, label=manifest['name'], path=path,
                        updated=updated, metadata=metadata, links=links, project_id=project)]
        for item in manifest['items']:
            name = filename(item['name'])
            records.append(dict(kind='asset', id=f'{ident}/{name}', label=name, project_id=project,
                                path=path.parent / 'images' / name, updated=updated,
                                metadata=dict(item, media_type='image',
                                              url=f'/api/images/source/{ident}/{urllib.parse.quote(name)}'),
                                links=[('member_of', 'dataset', ident)]))
        self.library.replace(f'dataset:{ident}', records)

    def index_workspace_files(self):
        records = []
        if self.reference.is_dir():
            location = self.reference
            candidates = [(path, 'reference') for path in sorted(location.rglob('*'))
                          if path.suffix.lower() in MEDIA_TYPES and path.is_file() and under(location, path)]
        else:
            candidates = []
        if self.use_existing:
            for folder, role in ((self.root / 'Reference', 'reference'), (self.root / 'examples', 'reference'),
                                 (self.pilot / 'profiles', 'profile'), (self.pilot / 'review', 'review'),
                                 (self.pilot / 'experiments', 'experiment'),
                                 (self.root / 'build' / 'local-media-preview', 'review')):
                if not folder.is_dir():
                    continue
                paths = folder.glob('*') if role == 'reference' else folder.rglob('*')
                candidates.extend((path, role) for path in paths if path.is_file() and under(folder, path))
            portable = self.pilot / 'portable'
            candidates.extend((path, 'experiment') for path in portable.glob('*') if path.is_file() and under(portable, path))
            build = self.root / 'build'
            for folder in (path for path in build.glob('*') if path.is_dir() and under(build, path)):
                if folder.resolve() == self.home:
                    continue
                documents = [path for pattern in ('*scenario*.md', '*scenario*.json', 'cues*.json') for path in folder.glob(pattern)
                             if path.is_file() and under(folder, path)]
                scripts = [path for pattern in ('game/script.rpy', '*/game/script.rpy') for path in folder.glob(pattern)
                           if path.is_file() and under(folder, path)]
                candidates.extend((path, 'legacy_document') for path in documents)
                candidates.extend((path.parent.parent, 'legacy_export') for path in scripts)
                if documents:
                    candidates.extend((path, 'review') for path in folder.rglob('*')
                                      if path.suffix.lower() in MEDIA_TYPES and path.is_file() and under(folder, path))
        seen = set()
        for path, role in candidates:
            suffix = path.suffix.lower()
            kind = ('export' if role == 'legacy_export' else 'asset' if suffix in MEDIA_TYPES
                    else ('cues' if path.name.startswith('cues') else 'scenario') if role == 'legacy_document'
                    else 'source' if role == 'reference' and suffix in ('.txt', '.md')
                    else 'prompt' if role == 'profile' and suffix == '.json' else
                    'artifact' if role == 'experiment' and suffix in ('.json', '.md') else None)
            if not kind or path in seen:
                continue
            seen.add(path)
            metadata = dict(role=role, media_type=MEDIA_TYPES.get(suffix))
            if path.is_file():
                metadata['size'] = path.stat().st_size
            records.append(dict(kind=kind, id=content_id(self.library.location(path)), label=path.name,
                                path=path, updated=timestamp(path), state='legacy' if role.startswith('legacy') else None,
                                metadata=metadata))
        tools = {'sd-cpp': ('stable-diffusion.cpp', self.engine()),
                 'sd-scripts': ('sd-scripts', self.trainer()), 'mflux': ('MFlux', self.mflux_environment())}
        for ident, (label, location) in tools.items():
            path = location[0] if isinstance(location, tuple) else location
            if path:
                records.append(dict(kind='tool', id=ident, label=label, path=path,
                                    updated=timestamp(path), state='installed'))
        if self.use_existing:
            audio_tools = {'aivis': ('AivisSpeech', 'aivis'), 'voicevox': ('VOICEVOX', 'voicevox'),
                           'kokoro': ('Kokoro', 'kokoro-int8-multi-lang-v1_1')}
            for ident, (label, folder) in audio_tools.items():
                path = self.legacy / folder
                if path.is_dir():
                    records.append(dict(kind='tool', id=ident, label=label, path=path, state='installed',
                                        updated=timestamp(path), metadata={'media_type': 'audio'}))
        self.library.replace('workspace_files', records)

    def index_models(self):
        records = []
        for ident, profile in TRAINING_BASES.items():
            path = self.training_base(ident)
            actual_revision = self.model_revision_record(ident) if path else None
            metadata = {key: profile[key] for key in ('name', 'family', 'revision', 'sha256', 'repo', 'url')
                        if key in profile}
            if actual_revision:
                metadata['installation'] = actual_revision
            records.append(dict(kind='model', id=ident, label=profile['label'],
                                path=path, state='installed' if path else 'not_installed', metadata=metadata))
        records.extend(dict(kind='model', id=f'openai:{model}', label=model, state='remote',
                            metadata={'provider': 'OpenAI'}) for model in ONLINE_MODELS)
        self.library.replace('models', records)

    def index_standalone_loras(self):
        records = []
        recipe = self.home / 'lora-transfer.json'
        legacy = json.loads(recipe.read_text(encoding='utf-8')).get('legacy', {}) if self.use_existing and recipe.is_file() else {}
        sources = self.sources() if legacy else {}
        for branch, details in legacy.items():
            source = sources.get(details['source'])
            if source:
                records.append(dict(kind='dataset', id=f'legacy:{branch}', label=details['name'], path=source[0],
                                    updated=timestamp(recipe), state='legacy',
                                    metadata=dict(count=len(details['images']), trigger=details['trigger'],
                                                  source=details['source'], training=details['training'])))
        legacy_ids = {record['id'] for record in records}
        for ident, (path, label, strength, family) in self.standalone_loras().items():
            dataset = f'legacy:{ident.split("-", 1)[0]}'
            links = [('uses_model', 'model', family)]
            if dataset in legacy_ids:
                links.append(('uses_dataset', 'dataset', dataset))
            parts = ident.split(':')
            project = parts[1] if parts[0] == 'import' and len(parts) >= 3 and JOB_ID.fullmatch(parts[1]) else None
            records.append(dict(kind='lora', id=ident, label=label, path=path, updated=timestamp(path), project_id=project,
                                metadata=dict(family=family, strength=strength), links=links))
        self.library.replace('standalone_loras', records)

    def index_export(self, record):
        ident = record['id']
        path = self.home / record['path']
        self.library.put(f'export:{ident}', dict(kind='export', id=ident, label=path.name, path=path,
                                                updated=record['created'], metadata=record['metadata'],
                                                links=record.get('links', [])))

    def record_export(self, ident, path, export_kind, *, links=(), metadata=None):
        info = dict(metadata or {}, export_kind=export_kind, size=path.stat().st_size if path.is_file() else None)
        info['ownership_version'] = 2
        directory = self.home / 'exports' / ident
        if path.parent == directory and path.name in ('prisma-lora.zip', 'prisma-renpy.zip', 'prisma-dataset.zip'):
            info['url'] = f'/api/exports/{ident}/{path.name}'
        record = dict(id=ident, path=self.library.location(path), created=timestamp(path),
                      metadata=info, links=list(links))
        directory.mkdir(parents=True, exist_ok=True)
        write_json(directory / 'export.json', record)
        self.index_export(record)

    def refresh_library(self, _=None):
        with self._library_lock:
            skipped = self.resources.refresh() + self.jobs.refresh_index()
            self.index_models()
            self.index_workspace_files()
            self.index_standalone_loras()
            datasets = []
            for path in self.dataset_root.glob('*/dataset.json'):
                if not JOB_ID.fullmatch(path.parent.name) or not under(self.dataset_root, path):
                    continue
                try:
                    manifest = json.loads(path.read_text(encoding='utf-8'))
                    if manifest['id'] != path.parent.name:
                        raise ValueError('Dataset ID does not match its directory.')
                    datasets.append(manifest)
                except (OSError, ValueError, KeyError, TypeError):
                    skipped += 1
            for manifest in datasets:
                self.index_dataset(manifest)
            for directory in (self.home / 'exports').glob('*'):
                if not JOB_ID.fullmatch(directory.name) or not under(self.home / 'exports', directory):
                    continue
                sidecar = directory / 'export.json'
                try:
                    if sidecar.is_file():
                        record = json.loads(sidecar.read_text(encoding='utf-8'))
                        if record['id'] != directory.name:
                            raise ValueError('Export ID does not match its directory.')
                        self.index_export(record)
                    else:
                        for name, kind in (('prisma-lora.zip', 'lora'), ('prisma-renpy.zip', 'renpy'),
                                           ('prisma-dataset.zip', 'dataset')):
                            if (directory / name).is_file():
                                self.record_export(directory.name, directory / name, kind)
                                break
                except (OSError, ValueError, KeyError, TypeError):
                    skipped += 1
            self.library.backfill_projects()
            self._library_ready = True
        return {'path': str(self.library.path), 'total': self.library.list(limit=1)['total'], 'skipped': skipped}

    def ensure_library(self):
        if not self._library_ready:
            with self._library_lock:
                if not self._library_ready:
                    self.refresh_library()

    def library_list(self, data):
        self.ensure_library()
        kind = data.get('kind')
        if kind is not None:
            choice(kind, KINDS, '记录类别')
        query = string(data.get('query', ''), '搜索词', 200, required=False)
        limit = number(data.get('limit', 30), '每页条数', 1, 100, integer=True)
        offset = number(data.get('offset', 0), '起始位置', 0, 2_147_483_647, integer=True)
        result = self.library.list(kind=kind, query=query, limit=limit, offset=offset,
                                   project=data.get('project_id'), shared=data.get('shared', False))
        for row in result['items']:
            self.library_job_state(row)
        return result

    def library_item(self, data):
        self.ensure_library()
        kind = choice(data.get('kind'), KINDS, '记录类别')
        ident = string(data.get('id'), '记录 ID', 500)
        item = self.library_job_state(self.library.get(kind, ident))
        metadata = item['metadata']
        if (kind in ('source', 'scenario', 'cues') and item['available']
                and metadata.get('role') in ('reference', 'legacy_document')):
            path = self.home / item['path']
            if under(self.root, path) and path.stat().st_size <= 4 * 1024 * 1024:
                try:
                    text = path.read_text(encoding='utf-8-sig')
                    if kind == 'cues':
                        metadata['cues'] = json.loads(text)
                    elif kind == 'scenario':
                        metadata['scenario'] = normalize_scenario(text)
                    else:
                        metadata['text'] = text
                except (OSError, ValueError):
                    pass
        if kind == 'scenario' and 'text' in metadata:
            metadata['scenario'] = normalize_scenario(metadata.pop('text'))
        return item

    def import_workspace_asset(self, data):
        project = data.get('project_id')
        if not data.get('library_id'):
            return self.resources.import_asset(data, project=project)
        if data.get('data') is not None or data.get('artifact') is not None:
            raise ValueError('请只选择一个素材来源。')
        item = self.library_item({'kind': 'asset', 'id': data['library_id']})
        name = data.get('name') or item['label']
        metadata = item['metadata']
        if metadata.get('artifact'):
            return self.resources.register_asset(name, artifact=metadata['artifact'], origin=item['id'], project=project)
        if not item['path']:
            raise FileNotFoundError('素材文件不存在。')
        path = self.home / item['path']
        if not path.is_file() or path.stat().st_size > MAX_UPLOAD:
            raise ValueError('素材文件不存在或超过 45 MiB。')
        if metadata.get('sha256') and metadata.get('url', '').startswith('/api/workspace/assets/'):
            return self.resources.save_asset(dict(metadata, name=name), origin=item['id'], project=project)
        return self.resources.register_asset(name, blob=path.read_bytes(), origin=item['id'], project=project)

    def library_job_state(self, row):
        active = self.jobs.active
        if (row['kind'] == 'job' and row['state'] in ('running', 'cancelling')
                and (active is None or active.directory.name != row['id'])):
            row['state'] = 'interrupted'
        return row

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

    def catalog(self, project=None):
        self.ensure_library()
        lora_ids = self.library.visible_ids('lora', project, include_shared=True)
        dataset_ids = self.library.visible_ids('dataset', project, include_shared=True)
        trainer = self.trainer()
        mflux = self.mflux_environment()
        trainer_path = trainer[0] if trainer else mflux[0] if mflux else None
        return {
            'online': {'ready': bool(os.environ.get('OPENAI_API_KEY', '').strip()), 'models': ONLINE_MODELS},
            'local': {'supported': self.windows, 'training_supported': self.training_supported,
                      'engine': str(self.engine() or ''), 'model': str(self.base() or ''),
                      'trainer': str(trainer_path or ''), 'workspace': str(self.home),
                      'reference': str(self.reference),
                      'training_device': {'cuda': 'NVIDIA CUDA 12.4', 'mps': 'Apple Metal (MPS / MLX)'}.get(
                          self.training_backend, '不支持本地训练'),
                      'managed_model': BASE_NAME, 'trainer_revision': TRAINER_REV,
                      'training_models': self.training_models(),
                      'trainer_python': MFLUX_PYTHON if mflux and not trainer else TRAIN_PYTHON,
                      'mflux_version': MFLUX_VERSION if mflux else None,
                      'trainer_lock_sha256': sha256_file(self.training_lock) if self.training_supported else None},
            'loras': [dict(id=key, label=label, weight=weight, family=family)
                      for key, (_, label, weight, family) in self.loras().items() if key in lora_ids],
            'sources': [dict(id=ident, label=label, saved=bool(JOB_ID.fullmatch(ident)),
                             item_count=len(self.dataset_manifest(ident)['items'])
                             if JOB_ID.fullmatch(ident) else None)
                        for ident, (_, label) in self.sources().items()
                        if not JOB_ID.fullmatch(ident) or ident in dataset_ids],
            'jobs': self.jobs.recent(project=project, shared=project is None),
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
            manifest = dict(id=ident, name=name, trigger=trigger, source=source, items=items)
            if project := data.get('project_id'):
                manifest['project_id'] = project
            write_json(staged / 'dataset.json', manifest)
            staged.rename(self.dataset_root / ident)
        self.index_dataset(manifest)
        return {'id': ident, 'name': name, 'count': len(items)}

    def dataset_manifest(self, ident, *, verify=False):
        if not isinstance(ident, str) or not JOB_ID.fullmatch(ident) or ident not in self.sources():
            raise FileNotFoundError('已保存的数据集不存在。')
        folder = self.dataset_root / ident
        manifest = json.loads(child_file(folder, 'dataset.json').read_text(encoding='utf-8'))
        if verify:
            images = folder / 'images'
            expected = set()
            image_stems = set()
            for item in manifest['items']:
                path = child_file(images, item['name'])
                caption = child_file(images, path.with_suffix('.txt').name)
                expected.update((path.name, caption.name))
                image_stems.add(path.stem)
                digest = sha256_file(path)
                if (item.get('sha256', digest) != digest
                        or caption.read_text(encoding='utf-8').strip() != item['caption']):
                    raise ValueError(f'数据集文件与保存时不一致：{path.name}。请另存数据集。')
                item['sha256'] = digest
            extras = {path.name for path in images.iterdir()} - expected
            for name in extras:
                cache_stem = Path(name).stem
                suffix = next((cache_stem[len(stem):] for stem in image_stems
                               if cache_stem.startswith(stem + '_')), None)
                is_sdxl_cache = suffix is not None and re.fullmatch(r'_(?:\d+x\d+_)?sdxl', suffix)
                is_text_cache = suffix == '_te_outputs'
                if not name.endswith('.npz') or not (is_sdxl_cache or is_text_cache):
                    raise ValueError('数据集目录与清单不一致。请另存数据集。')
        return manifest

    def stage_training_images(self, job, source, manifest):
        staged = job.directory / 'dataset-images'
        staged.mkdir()
        for item in manifest['items']:
            image = child_file(source, item['name'])
            caption = child_file(source, image.with_suffix('.txt').name)
            if sha256_file(image) != item['sha256']:
                raise ValueError(f'数据集文件在训练准备期间发生变化：{image.name}。')
            shutil.copy2(image, staged / image.name)
            shutil.copy2(caption, staged / caption.name)
        return staged

    def dataset_archive(self, ident, archive, *, manifest=None):
        manifest = manifest if manifest is not None else self.dataset_manifest(ident, verify=True)
        folder = self.dataset_root / ident / 'images'
        with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_STORED) as zipped:
            zipped.writestr('dataset.json', json.dumps(manifest, ensure_ascii=False, indent=2))
            for item in manifest['items']:
                for name in (item['name'], Path(item['name']).with_suffix('.txt').name):
                    zipped.write(child_file(folder, name), f'images/{name}')

    def dataset_export_path(self, ident, *, manifest=None):
        manifest = manifest if manifest is not None else self.dataset_manifest(ident, verify=True)
        export_id = content_id(['dataset-export', ident])[:16]
        directory = self.home / 'exports' / export_id
        path = directory / 'prisma-dataset.zip'
        with self._library_lock:
            directory.mkdir(parents=True, exist_ok=True)
            if not path.is_file():
                with tempfile.TemporaryDirectory(dir=directory, prefix='.export-') as temporary:
                    staged = Path(temporary) / path.name
                    self.dataset_archive(ident, staged, manifest=manifest)
                    staged.replace(path)
            if not (directory / 'export.json').is_file():
                links = [('includes', 'dataset', ident), *self.resources.ownership(manifest.get('project_id'))]
                self.record_export(export_id, path, 'dataset', links=links)
        return path

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
            project = data.get('project_id')
            if (self.dataset_root / ident / 'dataset.json').is_file():
                existing = self.dataset_manifest(ident)
                if existing.get('project_id') != project:
                    ident = content_id(['dataset-import', ident, project])[:16]
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
            if project:
                restored['project_id'] = project
            if (self.dataset_root / ident).exists():
                if self.dataset_manifest(ident, verify=True) != restored:
                    raise ValueError('已有不同内容的数据集使用同一个 ID。')
            else:
                write_json(output / 'dataset.json', restored)
                output.rename(self.dataset_root / ident)
        self.index_dataset(restored)
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
        family = choice(data.get('family', 'sd1'), tuple(IMPORTED_LORA_STRENGTHS), 'LoRA 底模系列')
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
        metadata = header.get('__metadata__', {})
        if isinstance(metadata, dict):
            architecture = ' '.join(str(metadata.get(key, '')).lower()
                                    for key in ('ss_base_model_version', 'modelspec.architecture'))
            known = ('anima' if 'anima' in architecture else
                     'sdxl' if any(token in architecture for token in ('sdxl', 'sd_xl', 'stable-diffusion-xl')) else
                     'sd1' if any(token in architecture for token in ('sd_v1', 'stable-diffusion-v1')) else None)
            if known and known != family:
                raise ValueError(f'文件标记的底模系列为 {known}，请重新选择底模系列。')
        project = data.get('project_id')
        if project is not None and (not isinstance(project, str) or not JOB_ID.fullmatch(project)):
            raise ValueError('项目 ID 格式无效。')
        if project:
            self.resources.project_record(project)
        directory = self.lora_root / 'projects' / project if project else self.lora_root
        prefix = f'import:{project}:' if project else 'import:'
        if family != 'sd1':
            directory = directory / family
            prefix += f'{family}:'
        directory.mkdir(parents=True, exist_ok=True)
        try:
            with (directory / f'{path.stem}.safetensors').open('xb') as stream:
                stream.write(blob)
        except FileExistsError as error:
            raise ValueError('已存在同名 LoRA，请重命名后导入。') from error
        with self._library_lock:
            self.index_standalone_loras()
        return {'id': prefix + path.stem, 'family': family}

    def prepare_lora(self, data):
        project = data.get('project_id')
        ident = string(data.get('id'), 'LoRA ID', 300)
        self.ensure_library()
        if project:
            self.resources.project_record(project)
        if ident not in self.library.visible_ids('lora', project, include_shared=True):
            raise ValueError('该 LoRA 不在当前项目或共享库。')
        item = self.loras().get(ident)
        if item is None or item[3] not in IMPORTED_LORA_STRENGTHS:
            raise ValueError('当前本地出图工具无法加载这个 LoRA。')
        _, label, weight, family = item
        if family == 'anima':
            ready = bool(self.apple_silicon and self.training_base('anima') and self.mflux_environment())
            hint = '本地生成需要 Apple Silicon、Anima 底模与 MFlux。'
        elif family == 'sdxl':
            ready = bool(self.apple_silicon and self.training_base('sdxl') and self.trainer())
            hint = '本地生成需要 Apple Silicon、SDXL 底模与 MPS 环境。'
        else:
            ready = bool((self.windows and self.engine() and self.base())
                         or (self.apple_silicon and self.training_base('sd1') and self.trainer()))
            hint = '请安装匹配的 SD 1.x 底模与本地推理环境。'
        return {'id': ident, 'label': label, 'ready': ready, 'hint': '' if ready else hint,
                'generation': {'mode': 'local', 'base_model': family, 'loras': [ident], 'weight': weight}}

    def provision(self, data):
        target = choice(data.get('target'), ('inference', 'training'), '安装范围')
        model_id = choice(data.get('base_model', 'sd1'), tuple(TRAINING_BASES), '训练底模')
        if model_id in ('iris3b', 'anima') and not self.apple_silicon:
            raise ValueError(f'{TRAINING_BASES[model_id]["label"]} 的 MFlux 训练目前需要 Apple Silicon。')
        if target == 'inference' and not self.windows:
            raise ValueError('Prisma 本地推理工具目前支持 Windows x64；Apple Silicon 可安装 MPS 训练环境。')
        if target == 'inference' and model_id in ('iris3b', 'anima'):
            raise ValueError(f'{TRAINING_BASES[model_id]["label"]} 在 Prisma 中作为 Apple Silicon LoRA 训练底模安装。')
        if target == 'inference' and model_id != 'sd1':
            raise ValueError('Prisma 本地推理目前使用 SD 1.x；SDXL 只能安装训练底模。')
        if target == 'training' and not self.training_supported:
            raise ValueError('本地训练目前支持 Windows x64/CUDA 和 Apple Silicon/MPS。')

        def work(job):
            self.tools.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(dir=self.tools, prefix='install-') as temp:
                stage = Path(temp)
                if model_id in ('iris3b', 'anima'):
                    self.install_mflux(job, stage)
                elif self.windows and not self.engine():
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
                if model_id in ('sd1', 'sdxl'):
                    model_path = self.training_base(model_id)
                    profile = TRAINING_BASES[model_id]
                    if model_path:
                        job.phase(f'校验已安装的 {profile["label"]} 底模')
                        if sha256_file(model_path) != profile['sha256']:
                            raise ValueError(f'{profile["label"]} 底模 SHA-256 不符；请替换模型文件后重试。')
                    else:
                        size = '2.13 GB' if model_id == 'sd1' else '6.94 GB'
                        self.payload(job, profile['url'], self.models / profile['name'],
                                     sha256=profile['sha256'], limit=profile['limit'],
                                     label=f'下载 {profile["label"]} 底模（{size}）')
                    if target == 'training' and not self.trainer():
                        self.install_trainer(job, stage)
                elif model_id == 'iris3b':
                    self.install_iris_base(job)
                elif model_id == 'anima':
                    self.install_anima_base(job)
            self.index_models()
            self.index_workspace_files()
            job.update(installation=self.catalog()['local'])
            if model_id == 'iris3b':
                revision = self.model_revision_record(model_id)
                runtime = json.loads((self.tools / 'mflux' / 'mflux-ready.json').read_text(encoding='utf-8'))
                job.update(inputs={'base': {'repo': revision['model_repo'], 'revision': revision['model_revision'],
                                           'weights': self.identity(self.models / 'iris-3b' / 'model.safetensors')},
                                   'text_encoder': revision['text_encoder'],
                                   'mflux': runtime, 'model_fetcher': self.identity(Path(__file__).with_name('mflux_model_fetch.py'))})
            elif model_id == 'anima':
                revision = self.model_revision_record(model_id)
                runtime = json.loads((self.tools / 'mflux' / 'mflux-ready.json').read_text(encoding='utf-8'))
                job.update(inputs={'base': {'repo': revision['model_repo'], 'revision': revision['model_revision'],
                                           'files': [self.identity(Path(item['path'])) for item in revision['files']]},
                                   'tokenizers': revision['tokenizers'], 'mflux': runtime,
                                   'model_fetcher': self.identity(Path(__file__).with_name('anima_model_fetch.py'))})

        return self.jobs.start('install', {'target': target, 'base_model': model_id}, work)

    def install_mflux(self, job, stage):
        if not self.apple_silicon:
            raise ValueError('MFlux 训练目前需要 Apple Silicon。')
        parent = self.tools / 'mflux'
        env_dir = parent / 'venv'
        python = env_dir / 'bin' / 'python'
        uv = first_file(self.tools / 'uv', Path(shutil.which('uv')) if shutil.which('uv') else None)
        if not uv:
            archive = stage / MAC_UV_ARCHIVE
            self.payload(job, MAC_UV_URL, archive, sha256=MAC_UV_HASH, limit=36_000_000,
                         label='下载固定版本的 Apple Silicon uv')
            extracted = stage / 'uv'
            extracted.mkdir()
            try:
                with tarfile.open(archive, 'r:gz') as source:
                    matches = [member for member in source.getmembers()
                               if Path(member.name).name == 'uv' and member.isfile()]
                    if len(matches) != 1 or matches[0].size > 64 * 1024 * 1024:
                        raise ValueError('uv 压缩包内容与预期不符。')
                    with source.extractfile(matches[0]) as read, (extracted / 'uv').open('xb') as write:
                        shutil.copyfileobj(read, write)
            except (tarfile.TarError, OSError) as error:
                raise ValueError('无法解压 Apple Silicon uv 工具。') from error
            uv = self.tools / 'uv'
            (extracted / 'uv').replace(uv)
            uv.chmod(0o755)

        uv_version = subprocess.run([uv, '--version'], capture_output=True, timeout=10, check=True).stdout.decode().strip()
        cache = self.tools / 'uv-cache'
        env = {'UV_CACHE_DIR': str(cache), 'UV_PYTHON_INSTALL_DIR': str(self.tools / 'python'),
               'UV_NO_MODIFY_PATH': '1', 'UV_NO_PROGRESS': '1', 'UV_LINK_MODE': 'hardlink'}
        parent.mkdir(parents=True, exist_ok=True)
        marker_path = parent / 'mflux-ready.json'
        try:
            marker = json.loads(marker_path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            marker = {}
        pyvenv = env_dir / 'pyvenv.cfg'
        valid_python = pyvenv.is_file() and re.search(
            rf'^version(?:_info)?\s*=\s*{re.escape(MFLUX_PYTHON)}(?:\.|\s|$)',
            pyvenv.read_text(encoding='utf-8'), re.MULTILINE)
        if not valid_python:
            job.phase(f'安装独立 Python {MFLUX_PYTHON} 与 MFlux 环境')
            job.run([uv, 'venv', '--clear', '--managed-python', '--python', MFLUX_PYTHON, env_dir], env=env)
        if (marker.get('mflux') != MFLUX_VERSION or marker.get('mflux_revision') != MFLUX_REVISION
                or not (env_dir / 'bin' / 'mflux-train').is_file()):
            job.phase(f'安装 MFlux {MFLUX_VERSION}（提交 {MFLUX_REVISION[:12]}）与 Apple MLX 依赖')
            job.run([uv, 'pip', 'install', '--python', python, '--index-url', 'https://pypi.org/simple',
                     '--reinstall', f'mflux @ git+{MFLUX_REPO}@{MFLUX_REVISION}'], env=env)
        job.phase('检查 MFlux、Anima / Iris 训练入口和 Apple MLX')
        versions_file = parent / 'mflux-ready.json'
        version_script = (
            'import importlib.metadata as m, json, pathlib, platform, sys; '
            'import mlx.core as mx; '
            'packages = {d.metadata["Name"].lower(): d.version for d in m.distributions()}; '
            'assert packages.get("mflux") == ' + repr(MFLUX_VERSION) + '; '
            'assert platform.machine().lower() in ("arm64", "aarch64"); '
            'assert hasattr(mx, "default_device"); '
            'print(json.dumps({"mflux": packages["mflux"], "mflux_repo": ' + repr(MFLUX_REPO) + ', '
            '"mflux_revision": ' + repr(MFLUX_REVISION) + ', "python": sys.version.split()[0], '
            '"machine": platform.machine(), "mlx": packages.get("mlx"), "uv": ' + repr(uv_version) + ', '
            '"packages": packages}, sort_keys=True), file=open(sys.argv[1], "w"))'
        )
        job.run([python, '-c', 'import sys; ' + version_script, versions_file], env=env)
        if not (env_dir / 'bin' / 'mflux-generate-anima').is_file():
            raise ValueError('MFlux 安装包没有 Anima 生成入口。')

    def install_iris_base(self, job):
        model_dir = self.models / 'iris-3b'
        metadata = self.models / 'iris-3b.prisma.json'
        fetcher = Path(__file__).with_name('mflux_model_fetch.py')
        python, _, _, parent = self.mflux_environment()
        self.models.mkdir(parents=True, exist_ok=True)
        job.phase('下载 Iris-3B Base 和文字编码器（约 20 GB）')
        fetch_env = {'HF_HUB_CACHE': str(self.tools / 'hf-cache'), 'HF_HUB_DISABLE_PROGRESS_BARS': '1',
                     'HF_XET_HIGH_PERFORMANCE': '1', 'HF_XET_FIXED_DOWNLOAD_CONCURRENCY': '4',
                     'HF_XET_CLIENT_READ_TIMEOUT': '120s',
                     'HF_XET_LOG_DEST': str(self.tools / 'hf-xet-logs') + os.sep}
        job.run([python, fetcher, '--model-dir', model_dir, '--cache-dir', self.tools / 'hf-cache',
                 '--metadata', metadata], env=fetch_env)
        model_record = self.model_revision_record('iris3b')
        base = self.identity(model_dir / 'model.safetensors')
        text = model_record['text_encoder']
        text_files = [self.identity(Path(path)) for path in text['files']]
        job.update(install_inputs={'base': dict(repo=model_record['model_repo'], revision=model_record['model_revision'],
                                                identity=base),
                                   'text_encoder': dict(repo=IRIS_TEXT_ENCODER,
                                                        revision=text['revision'], files=text_files),
                                   'mflux': json.loads((parent / 'mflux-ready.json').read_text(encoding='utf-8')),
                                   'fetcher': self.identity(fetcher)})

    def install_anima_base(self, job):
        model_dir = self.models / 'anima'
        metadata = self.models / 'anima.prisma.json'
        fetcher = Path(__file__).with_name('anima_model_fetch.py')
        self.models.mkdir(parents=True, exist_ok=True)
        job.phase('下载并校验 Anima Base、VAE 与分词器（约 5.7 GB）')
        fetch_env = {'HF_HUB_CACHE': str(self.tools / 'hf-cache'), 'HF_HUB_DISABLE_PROGRESS_BARS': '0',
                     'HF_HUB_DISABLE_XET': '1', 'PYTHONUNBUFFERED': '1'}
        python, _, _, _ = self.mflux_environment()
        job.run([python, fetcher, '--model-dir', model_dir, '--cache-dir', self.tools / 'hf-cache',
                 '--metadata', metadata], env=fetch_env)
        record = self.model_revision_record('anima')
        job.update(install_inputs={'base': {'repo': record['model_repo'], 'revision': record['model_revision'],
                                            'files': [self.identity(Path(item['path'])) for item in record['files']]},
                                   'tokenizers': record['tokenizers'],
                                   'mflux': json.loads((self.tools / 'mflux' / 'mflux-ready.json').read_text(
                                       encoding='utf-8')),
                                   'fetcher': self.identity(fetcher)})

    def install_trainer(self, job, stage):
        uv = first_file(self.tools / ('uv.exe' if self.windows else 'uv'))
        if not uv:
            available = shutil.which('uv')
            if available:
                try:
                    version = subprocess.run([available, '--version'], capture_output=True, timeout=5,
                                             check=True,
                                             creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
                    if version.stdout.split()[0:2] == [b'uv', UV_VERSION.encode()]:
                        uv = Path(available)
                except (OSError, subprocess.SubprocessError):
                    pass
        if not uv:
            if self.windows:
                archive = stage / UV_ARCHIVE
                self.payload(job, UV_URL, archive, sha256=UV_HASH, limit=36_000_000,
                             label='下载 Python 环境工具 uv')
                unpack(job, archive, stage / 'uv', only={'uv.exe'})
                uv = self.tools / 'uv.exe'
                (stage / 'uv' / 'uv.exe').rename(uv)
            else:
                archive = stage / MAC_UV_ARCHIVE
                self.payload(job, MAC_UV_URL, archive, sha256=MAC_UV_HASH, limit=36_000_000,
                             label='下载 Apple Silicon Python 环境工具 uv')
                extracted = stage / 'uv'
                extracted.mkdir()
                try:
                    with tarfile.open(archive, 'r:gz') as source:
                        matches = [member for member in source.getmembers()
                                   if Path(member.name).name == 'uv' and member.isfile()]
                        if len(matches) != 1 or matches[0].size > 64 * 1024 * 1024:
                            raise ValueError('uv 压缩包内容与预期不符。')
                        with source.extractfile(matches[0]) as read, (extracted / 'uv').open('xb') as write:
                            shutil.copyfileobj(read, write)
                except (tarfile.TarError, OSError) as error:
                    raise ValueError('无法解压 Apple Silicon uv 工具。') from error
                uv = self.tools / 'uv'
                (extracted / 'uv').replace(uv)
                uv.chmod(0o755)

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

        marker_path = self.tools / 'trainer-ready.json'
        try:
            marker = json.loads(marker_path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            marker = {}
        sdxl_tokenizers_valid = (marker.get('sdxl_model_revision') == SDXL_REVISION
                                 and marker.get('sdxl_tokenizer_hashes') ==
                                 self.tokenizer_hashes(self.tools / 'tokenizer-cache').get(SDXL_TOKENIZER))
        for name in SDXL_TOKENIZER_FILES:
            path = self.tools / 'tokenizer-cache' / SDXL_TOKENIZER / name
            if not sdxl_tokenizers_valid or not path.is_file():
                self.payload(job, f'{SDXL_TOKENIZER_URL}/{name}', path, sha256=None, limit=2_000_000,
                             label=f'下载 SDXL CLIP-G 分词文件 · {name}',
                             cache_name=f'sdxl-tokenizer2-{name}')

        env = {'UV_CACHE_DIR': os.environ.get('PRISMA_UV_CACHE', str(self.tools / 'uv-cache')),
               'UV_PYTHON_INSTALL_DIR': str(self.tools / 'python'), 'UV_NO_MODIFY_PATH': '1',
               'UV_NO_PROGRESS': '1', 'UV_LINK_MODE': 'hardlink'}
        python = self.training_python(self.tools)
        job.phase(f'安装独立 Python {TRAIN_PYTHON} 与虚拟环境')
        # Keep a usable environment on retries; install the exact interpreter
        # when upgrading an environment created by an older installer.
        version = self.tools / 'venv' / 'pyvenv.cfg'
        if not python.is_file() or not version.is_file() or not re.search(
            rf'^version(?:_info)?\s*=\s*{re.escape(TRAIN_PYTHON)}(?:\s|$)', version.read_text(), re.MULTILINE
        ):
            job.run([uv, 'venv', '--clear', '--managed-python', '--python', TRAIN_PYTHON, self.tools / 'venv'], env=env)
        if self.training_backend == 'cuda':
            job.phase('安装 CUDA 版 PyTorch 2.6（首次下载较大）')
            job.run([uv, 'pip', 'install', '--no-deps', '--python', python,
                     '--index-url', 'https://download.pytorch.org/whl/cu124',
                     'torch==2.6.0+cu124', 'torchvision==0.21.0+cu124'], env=env)
        else:
            job.phase(f'安装带 Apple MPS FP16 GradScaler 支持的 PyTorch {MPS_TRAIN_PYTORCH}')
            job.run([uv, 'pip', 'install', '--no-deps', '--python', python, '--index-url', 'https://pypi.org/simple',
                     f'torch=={MPS_TRAIN_PYTORCH}', f'torchvision=={MPS_TRAIN_TORCHVISION}'], env=env)
        job.phase('按版本锁安装 LoRA 训练依赖')
        job.run([uv, 'pip', 'install', '--no-deps', '--python', python, '--index-url', 'https://pypi.org/simple',
                 '--requirements', self.training_lock], env=env, cwd=trainer)
        job.run([uv, 'pip', 'check', '--python', python], env=env)
        job.phase(f'检查训练依赖和 {self.training_backend.upper()}')
        self.check_training_backend(job, python, trainer)
        torch_version = '2.6.0+cu124' if self.training_backend == 'cuda' else MPS_TRAIN_PYTORCH
        torchvision_version = '0.21.0+cu124' if self.training_backend == 'cuda' else MPS_TRAIN_TORCHVISION
        write_json(self.tools / 'trainer-ready.json', {'revision': TRAINER_REV, 'torch': torch_version,
                   'torchvision': torchvision_version, 'python': TRAIN_PYTHON, 'backend': self.training_backend,
                   'lock_sha256': sha256_file(self.training_lock),
                   'sdxl_model_revision': SDXL_REVISION,
                   'sdxl_tokenizer_hashes': self.tokenizer_hashes(self.tools / 'tokenizer-cache')[SDXL_TOKENIZER]})

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

    def check_training_backend(self, job, python, code):
        if self.training_backend == 'cuda':
            script = ('import torch, bitsandbytes, accelerate, diffusers, transformers, networks.lora; '
                      'assert torch.cuda.is_available(), "NVIDIA CUDA unavailable"; '
                      'print(torch.__version__, torch.cuda.get_device_name(0))')
        else:
            script = ('import torch, accelerate, diffusers, transformers, networks.lora\n'
                      'from accelerate import Accelerator\n'
                      'assert torch.backends.mps.is_built(), "PyTorch was built without MPS"\n'
                      'assert torch.backends.mps.is_available(), "Apple MPS unavailable"\n'
                      'accelerator = Accelerator(mixed_precision="fp16")\n'
                      'assert accelerator.device.type == "mps", f"Accelerate selected {accelerator.device}"\n'
                      'with accelerator.autocast():\n'
                      '    value = torch.ones((2, 2), device=accelerator.device) @ torch.ones((2, 2), device=accelerator.device)\n'
                      'assert value.sum().item() == 8\n'
                      'print(torch.__version__, "Apple MPS", accelerator.device, value.dtype)')
        try:
            job.run([python, '-c', script], cwd=code)
        except ValueError as error:
            hint = 'NVIDIA 驱动、CUDA 和任务日志' if self.training_backend == 'cuda' else 'macOS 版本、PyTorch MPS 和任务日志'
            raise ValueError(f'训练环境检查失败；请检查{hint}。') from error

    def generate(self, data, *, original=None):
        data = dict(data)
        if project := data.get('project_id'):
            defaults = self.resources.load_project({'id': project})['config']['generation']
            for key, value in {
                'mode': defaults['mode'], 'count': defaults['count'], 'model': defaults['online_model'],
                'size': defaults['online_size'], 'quality': defaults['quality'], 'negative': defaults['negative'],
                'base_model': defaults['base_model'], 'width': int(defaults['size'].split('x')[0]),
                'height': int(defaults['size'].split('x')[1]), 'cfg': defaults['cfg'], 'steps': defaults['steps'],
                'sampler': defaults['sampler'], 'weight': defaults['weight'], 'seed': defaults['seed'],
                'loras': [defaults['lora']] + ([defaults['compare']] if defaults['compare'] not in ('', 'none') else []),
            }.items():
                if key in ('model', 'base_model') and not value:
                    continue
                data.setdefault(key, value)
        mode = choice(data.get('mode'), ('online', 'local'), '生成方式')
        prompt = string(data.get('prompt'), '提示词', 4000)
        count = number(data.get('count', 1), '生成张数', 1, 4, integer=True)
        options = dict(mode=mode, prompt=prompt, count=count)
        if original:
            options['replay_of'] = original['id']

        def queue(work):
            selected = data.get('prompt_id')
            project_id = data.get('project_id')
            if selected:
                selected = string(selected, '提示词 ID', 64)
                saved = self.library_item({'kind': 'prompt', 'id': selected})['metadata']
            else:
                saved = {}
            negative = options.get('negative', '')
            if (saved.get('prompt') == prompt and saved.get('negative', '') == negative
                    and saved.get('project_id') == project_id):
                options['prompt_id'] = selected
            else:
                options['prompt_id'] = self.resources.prompt(prompt, negative, derived_from=selected, project=project_id)
            if project_id:
                options['project_id'] = project_id
            return self.jobs.start('generate', options, work)

        if mode == 'online':
            key = os.environ.get('OPENAI_API_KEY', '').strip()
            if not key:
                raise ValueError('请在启动 WebUI 前设置 OPENAI_API_KEY，然后重启服务。')
            options.update(model=choice(data.get('model', ONLINE_MODELS[0]), ONLINE_MODELS, '在线模型'),
                           size=choice(data.get('size', ONLINE_SIZES[0]), ONLINE_SIZES, '图片尺寸'),
                           quality=choice(data.get('quality', 'medium'), ('low', 'medium', 'high'), '质量'))
            return queue(lambda job: self.online_generate(job, options, key))

        negative = string(data.get('negative', ''), '负面提示词', 4000, required=False)
        if re.search(r'<\s*lora\s*[:>]', prompt + '\n' + negative, re.IGNORECASE):
            raise ValueError('请选择表单中的 LoRA；提示词无需填写 LoRA 标签。')
        first_seed = data.get('seed')
        seed = secrets.randbelow(2_147_483_647 - count) if first_seed is None else number(
            first_seed, 'Seed', 0, 2_147_483_647 - count + 1, integer=True)
        ids = data.get('loras', [''])
        found = self.loras()
        if (not isinstance(ids, list) or not 1 <= len(ids) <= 2
                or any(not isinstance(ident, str) or (ident and ident not in found) for ident in ids)
                or len(set(ids)) != len(ids)):
            raise ValueError('请选择一个 LoRA、底模，或两项不同的 LoRA 对照。')
        families = {found[ident][3] for ident in ids if ident}
        if len(families) > 1:
            raise ValueError('同一张对照图不能混用 SD 1.x 与 SDXL LoRA。')
        family = next(iter(families), None)
        if family is None:
            requested_base = data.get('base_model')
            if requested_base is not None:
                family = choice(requested_base, ('sd1', 'sdxl', 'anima'), '本地底模')
            elif self.apple_silicon and self.training_base('anima') and self.mflux_environment():
                family = 'anima'
            elif self.apple_silicon and self.training_base('sdxl') and self.trainer():
                family = 'sdxl'
            else:
                family = 'sd1'
        elif data.get('base_model') is not None and data['base_model'] != family:
            raise ValueError('所选 LoRA 与本地底模不属于同一模型系列。')
        if family == 'anima':
            trainer, model = self.mflux_environment(), self.training_base('anima')
            if not self.apple_silicon or not trainer or not model:
                raise ValueError('Anima 本地生成需要 Apple Silicon；请先安装 Anima Base 与 MFlux。')
            width = number(data.get('width', 1024), '宽度', 256, 1536, integer=True)
            height = number(data.get('height', 1024), '高度', 256, 1536, integer=True)
            if width % 16 or height % 16 or width * height > 2_359_296:
                raise ValueError('Anima 宽高须为 16 的倍数，且总像素不超过 2,359,296。')
            weight = number(data.get('weight', 1.0), 'LoRA 权重', 0.05, 1.5)
            if len(ids) > 1 and count > 2:
                raise ValueError('对照模式每个分支最多生成两张。')
            variants = []
            for ident in ids:
                path, label, _, _ = found.get(ident, (None, '底模', None, 'anima'))
                variants.append((path, ident, label, weight if path else None))
            sampler = choice(data.get('sampler', 'er_sde'), ANIMA_SAMPLERS, '采样器')
            prompt_text = prompt
            prompt_lower = prompt_text.lower()
            prompt_prefix = [token for token in ('masterpiece', 'best quality', 'score_7', 'safe')
                             if token not in prompt_lower]
            if (any(path for path, _, _, _ in variants)
                    and not re.search(r'\bprismaStyle\b', prompt_text, re.IGNORECASE)):
                prompt_prefix.append('prismaStyle')
            if prompt_prefix:
                prompt_text = f'{", ".join(prompt_prefix)}, {prompt_text}'
            options.update(model=TRAINING_BASES['anima']['model'], base_model='anima',
                           base_model_name=TRAINING_BASES['anima']['label'], resolution=512,
                           family='anima', prompt=prompt_text, negative=negative,
                           width=width, height=height, seed=seed,
                           steps=number(data.get('steps', 30), '采样步数', 1, 60, integer=True),
                           cfg=number(data.get('cfg', 4.5), 'CFG', 1, 15), sampler=sampler,
                           loras=[dict(id=ident, label=label, weight=strength)
                                  for _, ident, label, strength in variants])
            return queue(
                                   lambda job: self.local_generate_anima(job, options, trainer, model,
                                                                         variants, original))
        if family == 'sdxl':
            if not self.apple_silicon or not self.training_base('sdxl') or not self.trainer():
                raise ValueError('SDXL 本地试画目前需要 Apple Silicon，并先安装 SDXL 底模和 MPS 训练环境。')
            model = self.training_base('sdxl')
            profile = TRAINING_BASES['sdxl']
            width = number(data.get('width', 1024), '宽度', 256, 1536, integer=True)
            height = number(data.get('height', 1024), '高度', 256, 1536, integer=True)
            if width % 64 or height % 64 or width * height > 1024 * 1024:
                raise ValueError('SDXL 宽高须为 64 的倍数，且总像素不超过 1,048,576。')
            weight = number(data.get('weight', 0.65), 'LoRA 权重', 0.05, 1.5)
            if len(ids) > 1 and count > 2:
                raise ValueError('对照模式每个分支最多生成两张。')
            variants = []
            for ident in ids:
                path, label, default, _ = found.get(ident, (None, '底模', None, 'sdxl'))
                strength = weight if path else None
                variants.append((path, ident, label, strength))
            options.update(model=profile['name'], base_model='sdxl',
                           base_model_name=profile['label'], resolution=profile['resolution'],
                           family='sdxl', negative=negative, width=width, height=height, seed=seed,
                           steps=number(data.get('steps', 30), '采样步数', 1, 60, integer=True),
                           cfg=number(data.get('cfg', 5.5), 'CFG', 1, 15),
                           sampler=choice(data.get('sampler', 'euler_a'), DIFFUSERS_SAMPLERS, '采样器'),
                           loras=[dict(id=ident, label=label, weight=strength)
                                  for _, ident, label, strength in variants])

            def work_sdxl(job):
                job.phase('核对 SDXL 底模和 LoRA 文件')
                base_identity = self.identity(model)
                if base_identity['sha256'] != profile['sha256']:
                    raise ValueError('Animagine XL 底模 SHA-256 不符，请重新安装并校验模型。')
                runtime_file = job.directory / 'inference-runtime.json'
                python, code, _ = self.trainer()
                job.run([python, '-c', 'import importlib.metadata as m, json, pathlib, sys, torch; '
                         'packages = {d.metadata["Name"].lower(): d.version for d in m.distributions()}; '
                         'pathlib.Path(sys.argv[1]).write_text(json.dumps({"python": sys.version.split()[0], '
                         '"platform": sys.platform, "machine": __import__("platform").machine(), '
                         '"device": "mps", "torch": torch.__version__, '
                         '"diffusers": packages.get("diffusers"), "transformers": packages.get("transformers")}, '
                         'sort_keys=True), encoding="utf-8")', runtime_file], cwd=code)
                runner = Path(__file__).with_name('sdxl_preview_runner.py')
                inputs = {'base': base_identity,
                          'base_profile': {'repository': profile['repo'], 'revision': profile['revision'],
                                           'family': profile['family'], 'resolution': profile['resolution']},
                          'config': {'repository': SDXL_CONFIG_REPO, 'revision': SDXL_REVISION},
                          'loras': [self.identity(path) if path else None for path, _, _, _ in variants],
                          'runtime': json.loads(runtime_file.read_text(encoding='utf-8')),
                          'runner': self.identity(runner)}
                self.record_inputs(job, inputs, original)
                self.local_generate_sdxl(job, options, model, variants, runner, python, code)

            return queue(work_sdxl)

        if family == 'sd1' and self.apple_silicon:
            model = self.training_base('sd1')
            trainer = self.trainer()
            if not model or not trainer:
                raise ValueError('Apple Silicon 的 SD 1.x 预览需要 Anything V3 底模和 MPS 训练环境。')
            profile = TRAINING_BASES['sd1']
            width = number(data.get('width', 512), '宽度', 256, 1024, integer=True)
            height = number(data.get('height', 512), '高度', 256, 1024, integer=True)
            if width % 64 or height % 64 or width * height > 640 * 1024:
                raise ValueError('SD 1.x 宽高须为 64 的倍数，且总像素不超过 655,360。')
            weight = number(data.get('weight', 0.35), 'LoRA 权重', 0.05, 1.5)
            if len(ids) > 1 and count > 2:
                raise ValueError('对照模式每个分支最多生成两张。')
            variants = []
            for ident in ids:
                path, label, _, _ = found.get(ident, (None, '底模', None, 'sd1'))
                variants.append((path, ident, label, weight if path else None))
            options.update(model=profile['name'], base_model='sd1', base_model_name=profile['label'],
                           resolution=profile['resolution'], family='sd1', negative=negative,
                           width=width, height=height, seed=seed,
                           steps=number(data.get('steps', 30), '采样步数', 1, 60, integer=True),
                           cfg=number(data.get('cfg', 7.0), 'CFG', 1, 15),
                           sampler=choice(data.get('sampler', 'euler_a'), DIFFUSERS_SAMPLERS, '采样器'), clip_skip=2,
                           loras=[dict(id=ident, label=label, weight=strength)
                                  for _, ident, label, strength in variants])

            def work_mps_sd1(job):
                job.phase('核对 Anything V3 底模和 SD 1.x LoRA')
                base_identity = self.identity(model)
                if base_identity['sha256'] != profile['sha256']:
                    raise ValueError('Anything V3 底模 SHA-256 不符，无法生成 SD 1.x 预览。')
                python, code, _ = trainer
                runtime_file = job.directory / 'inference-runtime.json'
                job.run([python, '-c', 'import importlib.metadata as m, json, pathlib, sys, torch; '
                         'packages = {d.metadata["Name"].lower(): d.version for d in m.distributions()}; '
                         'pathlib.Path(sys.argv[1]).write_text(json.dumps({"python": sys.version.split()[0], '
                         '"platform": sys.platform, "machine": __import__("platform").machine(), '
                         '"device": "mps", "torch": torch.__version__, '
                         '"diffusers": packages.get("diffusers"), "transformers": packages.get("transformers")}, '
                         'sort_keys=True), encoding="utf-8")', runtime_file], cwd=code)
                runner = Path(__file__).with_name('sd1_preview_runner.py')
                inputs = {'base': base_identity,
                          'base_profile': {'revision': profile['revision'], 'family': profile['family'],
                                           'resolution': profile['resolution']},
                          'config': {'repository': SD1_CONFIG_REPO, 'revision': SD1_CONFIG_REVISION},
                          'loras': [self.identity(path) if path else None for path, _, _, _ in variants],
                          'runtime': json.loads(runtime_file.read_text(encoding='utf-8')),
                          'runner': self.identity(runner)}
                self.record_inputs(job, inputs, original)
                self.local_generate_mps_sd1(job, options, model, variants, runner, python, code)

            return queue(work_mps_sd1)

        model, engine = self.base(), self.engine()
        if not self.windows or not model or not engine:
            raise ValueError('请先在本地工具箱安装推理工具和底模。')
        width = number(data.get('width', 512), '宽度', 256, 1024, integer=True)
        height = number(data.get('height', 896), '高度', 256, 1024, integer=True)
        if width % 64 or height % 64 or width * height > 640 * 1024:
            raise ValueError('宽高须为 64 的倍数，且总像素不超过 655,360。')
        weight = number(data.get('weight', 0.35), 'LoRA 权重', 0.05, 1.5)
        if len(ids) > 1 and count > 2:
            raise ValueError('对照模式每个分支最多生成两张。')
        variants = []
        for ident in ids:
            path, label, default, _ = found.get(ident, (None, '底模', None, 'sd1'))
            variants.append((path, ident, label, (default if len(ids) > 1 else weight) if path else None))
        options.update(model=BASE_NAME, negative=negative, width=width, height=height, seed=seed,
                       steps=number(data.get('steps', 20), '采样步数', 1, 60, integer=True),
                       cfg=number(data.get('cfg', 6.5), 'CFG', 1, 15),
                       sampler=choice(data.get('sampler', 'euler_a'), DIFFUSERS_SAMPLERS, '采样器'), clip_skip=2,
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

        return queue(work)

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

    def local_generate_mps_sd1(self, job, options, model, variants, runner, python, code):
        directory = job.directory / 'images'
        directory.mkdir()
        config_path = job.directory / 'sd1-inference.json'
        variants_config = [{'lora': str(path) if path else None, 'label': label, 'weight': weight}
                           for path, _, label, weight in variants]
        config = {'model': str(model), 'config_repo': SD1_CONFIG_REPO,
                  'config_revision': SD1_CONFIG_REVISION, 'output_dir': str(directory),
                  'variants': variants_config, 'prompt': options['prompt'],
                  'negative': options['negative'], 'width': options['width'],
                  'height': options['height'], 'steps': options['steps'], 'cfg': options['cfg'],
                  'sampler': options['sampler'], 'clip_skip': options['clip_skip'],
                  'seed': options['seed'], 'count': options['count'],
                  'cache_dir': str(self.tools / 'hf-cache')}
        write_json(config_path, config)
        job.phase(f'本地 SD 1.x MPS 生成 · {options["count"] * len(variants)} 张')
        job.run([python, runner, '--config', config_path], cwd=code,
                env={'HF_HOME': str(self.tools / 'hf-cache'), 'PYTORCH_ENABLE_MPS_FALLBACK': '1',
                     'PYTHONFAULTHANDLER': '1'})
        images = []
        image_index = 0
        for _, _, label, weight in variants:
            for n in range(options['count']):
                image_index += 1
                name = f'sd1-{image_index:02d}.png'
                path = child_file(directory, name)
                with path.open('rb') as stream:
                    image_bytes(stream.read(16), '.png')
                images.append(dict(name=name, label=label, weight=weight,
                                   seed=options['seed'] + n, sha256=sha256_file(path)))
        job.update(images=images)

    def local_generate_anima(self, job, options, trainer, model, variants, original):
        _, train_cli, _, parent = trainer
        cli = parent / 'venv' / 'bin' / 'mflux-generate-anima'
        revision = self.model_revision_record('anima')
        profile = TRAINING_BASES['anima']
        if not revision or revision.get('model_revision') != profile['revision']:
            raise ValueError('Anima 底模版本与 Prisma 固定快照不符，请重新安装。')
        model_files = [self.identity(Path(item['path'])) for item in revision['files']]
        for identity, expected in zip(model_files, revision['files']):
            if identity['sha256'] != expected['sha256']:
                raise ValueError(f'Anima 底模文件校验失败：{Path(identity["path"]).name}')
        tokenizer_files = {}
        for repo, entry in revision['tokenizers'].items():
            identities = [self.identity(Path(item['path'])) for item in entry['files']]
            if any(identity['sha256'] != expected['sha256']
                   for identity, expected in zip(identities, entry['files'])):
                raise ValueError(f'Anima 分词器文件校验失败：{repo}')
            tokenizer_files[repo] = {'revision': entry['revision'], 'files': identities}
        if not cli.is_file():
            raise ValueError('MFlux 缺少 Anima 生成入口，请重新安装训练环境。')
        directory = job.directory / 'images'
        directory.mkdir()
        seeds = [options['seed'] + index for index in range(options['count'])]
        lora_inputs = [self.identity(path) if path else None for path, _, _, _ in variants]
        inputs = {
            'base_model': {'repo': revision['model_repo'], 'revision': revision['model_revision'],
                           'license': revision['model_license'], 'files': model_files},
            'tokenizers': tokenizer_files,
            'trainer': {'mflux': json.loads((parent / 'mflux-ready.json').read_text(encoding='utf-8')),
                        'generate_cli': self.identity(cli), 'train_cli': self.identity(train_cli)},
            'workflow': self.identity(Path(__file__)),
            'loras': lora_inputs,
            'generation': {'prompt': options['prompt'], 'negative': options['negative'],
                           'width': options['width'], 'height': options['height'], 'steps': options['steps'],
                           'guidance': options['cfg'], 'scheduler': options['sampler'], 'seeds': seeds},
        }
        self.record_inputs(job, inputs, original)
        environment = {'HF_HUB_CACHE': str(self.tools / 'hf-cache'), 'HF_HUB_OFFLINE': '1',
                       'MFLUX_CACHE_DIR': str(self.tools / 'mflux-cache'), 'PYTHONFAULTHANDLER': '1'}
        images = []
        total = len(variants) * len(seeds)
        for index, (path, _, label, weight) in enumerate(variants, 1):
            job.phase(f'本地 Anima / MLX 生成 · {label}' + (f' / LoRA {weight:g}' if path else ''))
            stem = f'anima-{index}-{{seed}}.png'
            command = [cli, '--model', str(model), '--base-model', 'anima-base',
                       '--prompt', options['prompt'], '--negative-prompt', options['negative'],
                       '--width', str(options['width']), '--height', str(options['height']),
                       '--steps', str(options['steps']), '--guidance', str(options['cfg']),
                       '--scheduler', options['sampler'], '--seed', *[str(seed) for seed in seeds],
                       '--output', str(directory / stem)]
            if path:
                command.extend(['--lora', str(path), f'{weight:g}'])
            job.update(command=[str(item) for item in command])
            job.run(command, cwd=job.directory, env=environment)
            for seed in seeds:
                expected_name = stem.format(seed=seed)
                seed_name = f'{Path(expected_name).stem}_seed_{seed}.png'
                name = next((candidate for candidate in (expected_name, seed_name)
                             if (directory / candidate).is_file()), expected_name)
                path_out = child_file(directory, name)
                with path_out.open('rb') as stream:
                    image_bytes(stream.read(16), '.png')
                images.append(dict(name=name, label=label, weight=weight, seed=seed,
                                   sha256=sha256_file(path_out)))
        job.update(images=images, note=f'Anima Base · {total} 张 · {options["width"]}×{options["height"]}')

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

    def local_generate_sdxl(self, job, options, model, variants, runner, python, code):
        directory = job.directory / 'images'
        directory.mkdir()
        config_path = job.directory / 'sdxl-inference.json'
        variants_config = []
        for path, ident, label, weight in variants:
            variants_config.append({'lora': str(path) if path else None, 'label': label, 'weight': weight})
        config = {'model': str(model), 'config_repo': SDXL_CONFIG_REPO, 'config_revision': SDXL_REVISION,
                  'output_dir': str(directory), 'variants': variants_config,
                  'prompt': options['prompt'], 'negative': options['negative'],
                  'width': options['width'], 'height': options['height'], 'steps': options['steps'],
                  'cfg': options['cfg'], 'sampler': options['sampler'], 'seed': options['seed'],
                  'count': options['count'], 'cache_dir': str(self.tools / 'hf-cache')}
        write_json(config_path, config)
        job.phase(f'本地 SDXL MPS 生成 · {options["count"] * len(variants)} 张')
        job.run([python, runner, '--config', config_path], cwd=code,
                env={'HF_HOME': str(self.tools / 'hf-cache'), 'PYTORCH_ENABLE_MPS_FALLBACK': '1',
                     'PYTHONFAULTHANDLER': '1'})
        images = []
        image_index = 0
        for _, _, label, weight in variants:
            for n in range(options['count']):
                image_index += 1
                name = f'sdxl-{image_index:02d}.png'
                path = child_file(directory, name)
                with path.open('rb') as stream:
                    image_bytes(stream.read(16), '.png')
                images.append(dict(name=name, label=label, weight=weight,
                                   seed=options['seed'] + n, sha256=sha256_file(path)))
        job.update(images=images)

    def train(self, data, *, original=None):
        data = dict(data)
        if project := data.get('project_id'):
            defaults = self.resources.load_project({'id': project})['config']['training']
            for key, value in {'base_model': defaults['base_model'] or 'sd1', 'dataset': defaults['dataset'],
                               'parent_lora': defaults['parent'] or None, 'name': defaults['name'],
                               'steps': defaults['steps'], 'save_every': defaults['save_every'],
                               'rank': defaults['rank'], 'lr': defaults['lr'], 'repeats': defaults['repeats'],
                               'seed': defaults['seed']}.items():
                data.setdefault(key, value)
        requested_model = data.get('base_model', 'sd1')
        if requested_model == BASE_NAME:
            requested_model = 'sd1'
        model_id = choice(requested_model, tuple(TRAINING_BASES), '训练底模')
        profile = TRAINING_BASES[model_id]
        if model_id in ('iris3b', 'anima'):
            trainer, model = self.mflux_environment(), self.training_base(model_id)
            if not self.apple_silicon:
                raise ValueError(f'{profile["label"]} 的 MFlux 训练目前需要 Apple Silicon。')
        else:
            trainer, model = self.trainer(), self.training_base(model_id)
        if not self.training_supported or not trainer or not model:
            raise ValueError('请先安装 LoRA 训练环境和底模。')
        dataset = data.get('dataset')
        sources = self.sources()
        if not isinstance(dataset, str) or not JOB_ID.fullmatch(dataset) or dataset not in sources:
            raise ValueError('请先保存一个数据集，再选中它开始训练。')
        parent_lora = data.get('parent_lora')
        parent_lora_record = None
        if parent_lora is not None:
            if not isinstance(parent_lora, str) or parent_lora not in self.loras():
                raise ValueError('续训 LoRA 不存在，请刷新图像环境后重新选择。')
            if model_id in ('iris3b', 'anima'):
                raise ValueError(f'{profile["label"]} 的续训目前不支持加载现有 LoRA。')
            parent_path, parent_label, _, parent_family = self.loras()[parent_lora]
            if parent_family != profile['family']:
                raise ValueError('续训 LoRA 必须与所选底模属于同一模型系列。')
            parent_lora_record = {'id': parent_lora, 'label': parent_label,
                                  'path': str(parent_path), 'family': parent_family}
        name = data.get('name')
        if not isinstance(name, str) or not SLUG.fullmatch(name) or windows_reserved_name(name):
            raise ValueError('LoRA 名称须为 1–48 个英文字母、数字、下划线或短横线，并以字母或数字开头。')
        mflux_model = model_id in ('iris3b', 'anima')
        default_steps = (len(self.dataset_manifest(dataset)['items']) * 25 if model_id == 'anima'
                         else 400 if model_id == 'iris3b' else 200)
        requested_steps = number(data.get('steps', default_steps), '训练步数', 1, 5000, integer=True)
        mflux_epochs = None
        steps = requested_steps
        if mflux_model:
            item_count = len(self.dataset_manifest(dataset)['items'])
            if item_count < 1:
                raise ValueError('训练数据集为空。')
            mflux_epochs = max(1, round(requested_steps / item_count))
            steps = item_count * mflux_epochs
        save_default = min(500 if model_id == 'anima' else 100, steps)
        save_every = number(data.get('save_every', save_default), '保存间隔', 1,
                            max(requested_steps, steps), integer=True)
        save_every = min(save_every, steps)
        options = dict(dataset=dataset, dataset_name=sources[dataset][1], name=name, steps=steps,
                       save_every=save_every,
                       rank=number(data.get('rank', 16 if mflux_model else 8 if model_id == 'sdxl' else 4), 'Rank', 2, 64, integer=True),
                       lr=number(data.get('lr', 0.0001), '学习率', 0.000001, 0.001),
                       repeats=1 if mflux_model else number(data.get('repeats', 10), '重复次数', 1, 50, integer=True),
                       seed=number(data.get('seed', 1065270805), '训练 Seed', 0, 2_147_483_647, integer=True),
                       base_model=model_id, base_model_name=profile['label'],
                       resolution=profile['resolution'], parent_lora=parent_lora,
                       parent_lora_name=parent_lora_record['label'] if parent_lora_record else None)
        if mflux_model:
            options.update(requested_steps=requested_steps, epochs=mflux_epochs, backend='mlx')
        if project := data.get('project_id'):
            options['project_id'] = project
        if model_id == 'anima':
            options.update(style_trigger='prismaStyle')
        if steps // options['save_every'] > 30:
            raise ValueError('请增大保存间隔，每次训练最多保存约 30 个中间权重。')
        if original:
            options['replay_of'] = original['id']
        directory = sources[dataset][0]

        def work(job):
            job.phase('核对底模和训练数据集')
            manifest = self.dataset_manifest(dataset, verify=True)
            if model_id == 'iris3b':
                self.train_iris(job, options, trainer, model, directory, manifest, original)
                return
            if model_id == 'anima':
                self.train_anima(job, options, trainer, model, directory, manifest, original)
                return
            python, code, cache = trainer
            managed = code.parent == self.tools
            runtime_file = job.directory / 'runtime.json'
            job.run([python, '-c', 'import importlib.metadata as m, json, pathlib, sys; '
                     'import platform, torch; '
                     'packages = {d.metadata["Name"].lower(): d.version for d in m.distributions()}; '
                     'mps = bool(torch.backends.mps.is_available()) if hasattr(torch.backends, "mps") else False; '
                     'pathlib.Path(sys.argv[1]).write_text(json.dumps({"python": sys.version.split()[0], '
                     '"platform": platform.platform(), "machine": platform.machine(), '
                     f'"training_backend": "{self.training_backend}", "mps_available": mps, '
                     '"packages": packages}, sort_keys=True), encoding="utf-8")', runtime_file], cwd=code)
            script = code / ('sdxl_train_network.py' if model_id == 'sdxl' else 'train_network.py')
            if not script.is_file():
                raise ValueError(f'训练工具缺少 {script.name}。请重新安装训练环境。')
            base_identity = self.identity(model)
            if base_identity['sha256'] != profile['sha256']:
                raise ValueError(f'{profile["label"]} 底模校验值不符，请重新安装并校验模型。')
            inputs = {'base': base_identity,
                      'base_profile': {'id': model_id, 'repository': profile.get('repo'),
                                       'revision': profile['revision'],
                                       'family': profile['family'], 'resolution': profile['resolution']},
                      'dataset': manifest,
                      'parent_lora': self.identity(Path(parent_lora_record['path']))
                      if parent_lora_record else None,
                      'trainer_revision': TRAINER_REV if managed else None,
                      'trainer_lock_sha256': sha256_file(self.training_lock) if managed else None,
                      'training_backend': self.training_backend,
                      'trainer_script': self.identity(script),
                      'loaders': [self.identity(code / 'library' / name) for name in (
                          ('sdxl_model_util.py', 'sdxl_train_util.py', 'strategy_sdxl.py')
                          if model_id == 'sdxl' else ('model_util.py',))],
                      'runner': self.identity(Path(__file__).with_name('lora_train_runner.py')),
                      'runtime': json.loads(runtime_file.read_text(encoding='utf-8')),
                      'tokenizers': self.tokenizer_hashes(cache)}
            self.record_inputs(job, inputs, original)
            job.phase('准备隔离的训练数据副本')
            training_images = self.stage_training_images(job, directory, manifest)
            self.train_lora(job, options, trainer, model, training_images, model_id)

        return self.jobs.start('train', options, work)

    def train_iris(self, job, options, trainer, model, images, manifest, original):
        python, cli, env_dir, parent = trainer
        revision = self.model_revision_record('iris3b')
        runtime = json.loads((parent / 'mflux-ready.json').read_text(encoding='utf-8'))
        text_encoder = revision['text_encoder']
        inputs = {
            'base_model': {'repo': revision['model_repo'], 'revision': revision['model_revision'],
                           'weights': self.identity(model / 'model.safetensors'),
                           'config': self.identity(model / 'config.yaml')},
            'text_encoder': {'repo': text_encoder['repo'], 'revision': text_encoder['revision'],
                             'files': [self.identity(Path(path)) for path in text_encoder['files']]},
            'dataset': manifest,
            'trainer': {'mflux': runtime['mflux'], 'mlx': runtime['mlx'], 'python': runtime['python'],
                        'packages': runtime['packages'], 'ready_sha256': sha256_file(parent / 'mflux-ready.json')},
            'mflux_train_cli': self.identity(cli),
            'workflow': self.identity(Path(__file__)),
            'training_backend': 'mlx',
            'training_config': {'seed': options['seed'], 'updates': options['steps'],
                                'requested_updates': options['requested_steps'], 'epochs': options['epochs'],
                                'resolution': options['resolution'], 'rank': options['rank'],
                                'learning_rate': options['lr'], 'gradient_checkpointing': True,
                                'cache_limit_gb': 2, 'batch_size': 1},
        }
        self.record_inputs(job, inputs, original)

        training_data = job.directory / 'dataset'
        training_data.mkdir()
        for item in manifest['items']:
            job.check()
            for name in (item['name'], Path(item['name']).with_suffix('.txt').name):
                source = child_file(images, name)
                target = training_data / filename(name)
                shutil.copy2(source, target)
        first = manifest['items'][0]
        preview_prompt = child_file(training_data, Path(first['name']).with_suffix('.txt').name).read_text(
            encoding='utf-8').strip()
        (training_data / 'preview_style.txt').write_text(preview_prompt, encoding='utf-8')

        # MFlux adds a timestamp to an existing checkpoint.output_path. Give it a
        # fresh child directory so its result location remains deterministic.
        output = job.directory / 'mflux-output'
        config_path = job.directory / 'mflux-train.json'
        config = {
            'model': TRAINING_BASES['iris3b']['model'],
            'data': training_data.as_posix(),
            'seed': options['seed'],
            'steps': 20,
            'guidance': 3.0,
            'max_resolution': options['resolution'],
            'gradient_checkpointing': True,
            'cache_limit_gb': 2,
            'training_loop': {'num_epochs': options['epochs'], 'batch_size': 1},
            'optimizer': {'name': 'AdamW', 'learning_rate': options['lr']},
            'checkpoint': {'output_path': output.as_posix(), 'save_frequency': options['save_every']},
            'monitoring': {'plot_frequency': 25, 'generate_image_frequency': options['save_every']},
            'lora_layers': {'targets': [
                {'module_path': 'blocks.{block}.attn.q_proj_x', 'blocks': {'start': 0, 'end': 8}, 'rank': options['rank']},
                {'module_path': 'blocks.{block}.attn.q_proj_y', 'blocks': {'start': 0, 'end': 8}, 'rank': options['rank']},
                {'module_path': 'blocks.{block}.attn.k_proj_x', 'blocks': {'start': 0, 'end': 8}, 'rank': options['rank']},
                {'module_path': 'blocks.{block}.attn.k_proj_y', 'blocks': {'start': 0, 'end': 8}, 'rank': options['rank']},
                {'module_path': 'blocks.{block}.attn.v_proj_x', 'blocks': {'start': 0, 'end': 8}, 'rank': options['rank']},
                {'module_path': 'blocks.{block}.attn.v_proj_y', 'blocks': {'start': 0, 'end': 8}, 'rank': options['rank']},
                {'module_path': 'blocks.{block}.attn.proj_x', 'blocks': {'start': 0, 'end': 8}, 'rank': options['rank']},
                {'module_path': 'blocks.{block}.attn.proj_y', 'blocks': {'start': 0, 'end': 8}, 'rank': options['rank']},
                {'module_path': 'blocks.{block}.attn_gate_x', 'blocks': {'start': 0, 'end': 8}, 'rank': options['rank']},
                {'module_path': 'blocks.{block}.attn_gate_y', 'blocks': {'start': 0, 'end': 8}, 'rank': options['rank']},
                {'module_path': 'blocks.{block}.mlp_x.w1', 'blocks': {'start': 0, 'end': 8}, 'rank': options['rank']},
                {'module_path': 'blocks.{block}.mlp_x.w2', 'blocks': {'start': 0, 'end': 8}, 'rank': options['rank']},
                {'module_path': 'blocks.{block}.mlp_x.w3', 'blocks': {'start': 0, 'end': 8}, 'rank': options['rank']},
                {'module_path': 'blocks.{block}.mlp_y.w1', 'blocks': {'start': 0, 'end': 8}, 'rank': options['rank']},
                {'module_path': 'blocks.{block}.mlp_y.w2', 'blocks': {'start': 0, 'end': 8}, 'rank': options['rank']},
                {'module_path': 'blocks.{block}.mlp_y.w3', 'blocks': {'start': 0, 'end': 8}, 'rank': options['rank']},
                {'module_path': 'blocks.{block}.q_proj', 'blocks': {'start': 8, 'end': 24}, 'rank': options['rank']},
                {'module_path': 'blocks.{block}.k_proj', 'blocks': {'start': 8, 'end': 24}, 'rank': options['rank']},
                {'module_path': 'blocks.{block}.v_proj', 'blocks': {'start': 8, 'end': 24}, 'rank': options['rank']},
                {'module_path': 'blocks.{block}.attn_gate', 'blocks': {'start': 8, 'end': 24}, 'rank': options['rank']},
                {'module_path': 'blocks.{block}.attn_proj', 'blocks': {'start': 8, 'end': 24}, 'rank': options['rank']},
                {'module_path': 'blocks.{block}.mlp.w1', 'blocks': {'start': 8, 'end': 24}, 'rank': options['rank']},
                {'module_path': 'blocks.{block}.mlp.w2', 'blocks': {'start': 8, 'end': 24}, 'rank': options['rank']},
                {'module_path': 'blocks.{block}.mlp.w3', 'blocks': {'start': 8, 'end': 24}, 'rank': options['rank']},
            ]},
        }
        write_json(config_path, config)
        command = [cli, '--config', config_path]
        job.update(command=[str(item) for item in command], runtime=runtime,
                   note=f'Iris-3B Base · {options["epochs"]} epochs × {len(manifest["items"])} images '
                        f'= {options["steps"]} updates at {options["resolution"]} px')
        job.phase(f'MLX LoRA 训练 · {options["name"]}（{options["steps"]} 步 / {options["epochs"]} 轮）')
        environment = {'HF_HUB_CACHE': str(self.tools / 'hf-cache'), 'HF_HUB_OFFLINE': '1',
                       'MFLUX_CACHE_DIR': str(self.tools / 'mflux-cache'), 'PYTHONFAULTHANDLER': '1'}
        job.run(command, cwd=job.directory, env=environment)

        source_checkpoints = output / 'checkpoints'
        checkpoint_dir = job.directory / 'checkpoints'
        checkpoint_dir.mkdir(parents=True, exist_ok=True)
        for path in sorted(source_checkpoints.glob('*.zip')):
            if path.is_file() and under(source_checkpoints, path):
                shutil.copy2(path, checkpoint_dir / path.name)
        checkpoints = []
        for path in sorted(checkpoint_dir.glob('*.zip')) + sorted(checkpoint_dir.glob('*.safetensors')):
            if not path.is_file() or not under(checkpoint_dir, path):
                continue
            match = re.search(r'(\d+)', path.stem)
            step = int(match[1]) if match else options['steps']
            checkpoints.append(dict(name=path.name, label=f'{options["name"]} · {step} 步',
                                    id=f'{job.directory.name}:{path.stem}', sha256=sha256_file(path)))
        if not checkpoints:
            raise ValueError('MFlux 没有生成可用 checkpoint。请查看任务日志。')
        previews = []
        image_dir = job.directory / 'images'
        for path in sorted((output / 'preview').glob('*.png')):
            if path.is_file() and under(output / 'preview', path):
                image_dir.mkdir(exist_ok=True)
                preview = image_dir / path.name
                shutil.copy2(path, preview)
                previews.append(dict(name=preview.name, label=f'Iris-3B 训练预览 · {path.stem}', seed=None,
                                     sha256=sha256_file(preview)))
        final_zip = max((checkpoint_dir / item['name'] for item in checkpoints
                         if item['name'].endswith('.zip')), key=lambda path: path.name, default=None)
        if final_zip is not None:
            with zipfile.ZipFile(final_zip) as archive:
                adapters = [name for name in archive.namelist() if name.endswith('_adapter.safetensors')]
                if adapters:
                    adapter_name = f'{options["name"]}.safetensors'
                    adapter_path = checkpoint_dir / adapter_name
                    with archive.open(adapters[-1]) as source, adapter_path.open('wb') as target:
                        shutil.copyfileobj(source, target)
                    step = int(re.search(r'(\d+)', final_zip.stem)[1])
                    checkpoints.append(dict(name=adapter_name,
                                            label=f'{options["name"]} · {step} 步 LoRA',
                                            id=f'{job.directory.name}:{Path(adapter_name).stem}',
                                            sha256=sha256_file(adapter_path)))
        loss_plot = output / 'loss' / 'loss.html'
        updates = dict(checkpoints=checkpoints, images=previews, mflux_output=str(output))
        if loss_plot.is_file() and under(output, loss_plot):
            updates['loss_plot'] = self.identity(loss_plot)
        job.update(**updates)

    def train_anima(self, job, options, trainer, model, images, manifest, original):
        python, cli, env_dir, parent = trainer
        revision = self.model_revision_record('anima')
        runtime = json.loads((parent / 'mflux-ready.json').read_text(encoding='utf-8'))
        if not revision or revision.get('model_revision') != TRAINING_BASES['anima']['revision']:
            raise ValueError('Anima 底模快照与 Prisma 固定版本不符，请重新安装并校验模型。')
        model_files = [self.identity(Path(item['path'])) for item in revision['files']]
        if any(identity['sha256'] != expected['sha256']
               for identity, expected in zip(model_files, revision['files'])):
            raise ValueError('Anima 底模文件校验失败，请重新安装。')
        tokenizers = {repo: {'revision': item['revision'],
                             'files': [self.identity(Path(file['path'])) for file in item['files']]}
                      for repo, item in revision['tokenizers'].items()}
        for repo, tokenizer in tokenizers.items():
            expected_files = revision['tokenizers'][repo]['files']
            if any(identity['sha256'] != expected['sha256']
                   for identity, expected in zip(tokenizer['files'], expected_files)):
                raise ValueError(f'Anima 分词器校验失败：{repo}')
        inputs = {
            'base_model': {'repo': revision['model_repo'], 'revision': revision['model_revision'],
                           'license': revision['model_license'], 'files': model_files},
            'tokenizers': tokenizers,
            'dataset': manifest,
            'trainer': {'mflux': runtime['mflux'], 'mlx': runtime['mlx'], 'python': runtime['python'],
                        'packages': runtime['packages'], 'ready_sha256': sha256_file(parent / 'mflux-ready.json')},
            'mflux_train_cli': self.identity(cli),
            'workflow': self.identity(Path(__file__)),
            'training_backend': 'mlx',
            'training_config': {'seed': options['seed'], 'updates': options['steps'],
                                'requested_updates': options['requested_steps'], 'epochs': options['epochs'],
                                'resolution': options['resolution'], 'rank': options['rank'],
                                'learning_rate': options['lr'], 'gradient_checkpointing': True,
                                'cache_limit_gb': 2, 'batch_size': 1, 'trigger': options['style_trigger']},
        }

        training_data = job.directory / 'dataset'
        training_data.mkdir()
        source_images = self.dataset_root / options['dataset'] / 'images'
        if not under(self.dataset_root, source_images) or not source_images.is_dir():
            raise ValueError('已保存的数据集图片目录不存在，请重新保存数据集。')
        job.phase('准备 Anima 训练数据副本')
        for item in manifest['items']:
            job.check()
            for name in (item['name'], Path(item['name']).with_suffix('.txt').name):
                source = child_file(source_images, name)
                target = training_data / filename(name)
                shutil.copy2(source, target)

        output = job.directory / 'mflux-output'
        config_path = job.directory / 'mflux-train.json'
        target_modules = (
            'self_attn.q_proj', 'self_attn.k_proj', 'self_attn.v_proj', 'self_attn.output_proj',
            'cross_attn.q_proj', 'cross_attn.k_proj', 'cross_attn.v_proj', 'cross_attn.output_proj',
            'mlp.layer1', 'mlp.layer2',
            'adaln_modulation_self_attn.1', 'adaln_modulation_self_attn.2',
            'adaln_modulation_cross_attn.1', 'adaln_modulation_cross_attn.2',
            'adaln_modulation_mlp.1', 'adaln_modulation_mlp.2',
        )
        config = {
            'model': TRAINING_BASES['anima']['model'],
            'model_path': model.as_posix(),
            'data': training_data.as_posix(),
            'seed': options['seed'],
            'steps': 30,
            'guidance': 4.0,
            'max_resolution': options['resolution'],
            'gradient_checkpointing': True,
            'cache_limit_gb': 2,
            'training_loop': {'num_epochs': options['epochs'], 'batch_size': 1},
            'optimizer': {'name': 'AdamW', 'learning_rate': options['lr']},
            'checkpoint': {'output_path': output.as_posix(), 'save_frequency': options['save_every']},
            'monitoring': {'preview_width': 512, 'preview_height': 512,
                           'plot_frequency': 50, 'generate_image_frequency': options['save_every']},
            'lora_layers': {'targets': [
                {'module_path': f'blocks.{{block}}.{module}', 'blocks': {'start': 0, 'end': 28},
                 'rank': options['rank']} for module in target_modules
            ]},
        }
        write_json(config_path, config)
        inputs['training_config']['config_file'] = self.identity(config_path)
        inputs['training_config']['model'] = TRAINING_BASES['anima']['model']
        inputs['training_config']['model_path'] = model.as_posix()
        inputs['training_config']['local_model_inventory'] = str(model)
        self.record_inputs(job, inputs, original)
        command = [cli, '--config', config_path]
        job.update(command=[str(item) for item in command], runtime=runtime,
                   note=f'Anima Base · {options["epochs"]} epochs × {len(manifest["items"])} images '
                        f'= {options["steps"]} updates at {options["resolution"]} px')
        job.phase(f'Anima / MLX LoRA 训练 · {options["name"]}（{options["steps"]} 步 / {options["epochs"]} 轮）')
        environment = {'HF_HUB_CACHE': str(self.tools / 'hf-cache'), 'HF_HUB_OFFLINE': '1',
                       'MFLUX_CACHE_DIR': str(self.tools / 'mflux-cache'), 'PYTHONFAULTHANDLER': '1'}
        job.run(command, cwd=job.directory, env=environment)

        source_checkpoints = output / 'checkpoints'
        checkpoint_dir = job.directory / 'checkpoints'
        checkpoint_dir.mkdir(parents=True, exist_ok=True)
        for path in sorted(source_checkpoints.glob('*.zip')):
            if path.is_file() and under(source_checkpoints, path):
                shutil.copy2(path, checkpoint_dir / path.name)
        checkpoints = []
        for path in sorted(checkpoint_dir.glob('*.zip')):
            if not path.is_file() or not under(checkpoint_dir, path):
                continue
            match = re.search(r'(\d+)', path.stem)
            step = int(match[1]) if match else options['steps']
            checkpoints.append(dict(name=path.name, label=f'{options["name"]} · {step} 步 checkpoint',
                                    id=f'{job.directory.name}:{path.stem}', sha256=sha256_file(path)))
        if not checkpoints:
            raise ValueError('MFlux 没有生成可用 Anima checkpoint。请查看任务日志。')
        final_zip = max((checkpoint_dir / item['name'] for item in checkpoints),
                        key=lambda path: int(re.search(r'(\d+)', path.stem)[1])
                        if re.search(r'(\d+)', path.stem) else -1)
        adapter_name = f'{options["name"]}.safetensors'
        adapter_path = checkpoint_dir / adapter_name
        with zipfile.ZipFile(final_zip) as archive:
            adapters = [name for name in archive.namelist() if name.endswith('_adapter.safetensors')]
            if not adapters:
                raise ValueError('最终 Anima checkpoint 中没有 LoRA 权重。')
            with archive.open(adapters[-1]) as source, adapter_path.open('wb') as target:
                shutil.copyfileobj(source, target)
        final_step = int(re.search(r'(\d+)', final_zip.stem)[1])
        checkpoints.append(dict(name=adapter_name, label=f'{options["name"]} · {final_step} 步 LoRA',
                                id=f'{job.directory.name}:{Path(adapter_name).stem}',
                                sha256=sha256_file(adapter_path)))

        previews = []
        image_dir = job.directory / 'images'
        for path in sorted((output / 'preview').glob('*.png')):
            if path.is_file() and under(output / 'preview', path):
                image_dir.mkdir(exist_ok=True)
                preview = image_dir / path.name
                shutil.copy2(path, preview)
                previews.append(dict(name=preview.name, label=f'Anima 训练预览 · {path.stem}', seed=None,
                                     sha256=sha256_file(preview)))
        updates = dict(checkpoints=checkpoints, images=previews, mflux_output=str(output))
        loss_plot = output / 'loss' / 'loss.html'
        if loss_plot.is_file() and under(output, loss_plot):
            updates['loss_plot'] = self.identity(loss_plot)
        job.update(**updates)

    def recover_iris_training(self, ident):
        record_dir = self.jobs.directory(ident)
        record = self.jobs.get(ident)
        if record.get('state') == 'done':
            return record
        if record.get('kind') != 'train' or record.get('options', {}).get('base_model') != 'iris3b':
            raise ValueError('只支持恢复已完成但未登记的 Iris-3B 训练任务。')
        if record.get('state') not in ('failed', 'interrupted'):
            raise ValueError('该 Iris-3B 任务当前状态不适合恢复。')

        expected_steps = int(record['options']['steps'])
        outputs = []
        for directory in sorted(self.jobs.root.glob(f'{ident}_*')):
            source = directory / 'checkpoints'
            archives = sorted(source.glob('*.zip')) if source.is_dir() else []
            if not directory.is_dir() or not archives:
                continue
            final = max(archives, key=lambda path: int(re.search(r'(\d+)', path.stem)[1])
                        if re.search(r'(\d+)', path.stem) else -1)
            match = re.search(r'(\d+)', final.stem)
            if not match or int(match[1]) < expected_steps:
                continue
            try:
                with zipfile.ZipFile(final) as archive:
                    adapters = [name for name in archive.namelist() if name.endswith('_adapter.safetensors')]
                    if not adapters or archive.getinfo(adapters[-1]).file_size <= 8:
                        continue
            except (OSError, zipfile.BadZipFile, KeyError):
                continue
            outputs.append((int(match[1]), directory, archives, final, adapters[-1]))
        if not outputs:
            raise ValueError('没有找到包含最终 LoRA 权重的完整 MFlux checkpoint。')

        step, output, archives, final, adapter_member = max(outputs, key=lambda item: item[0])
        checkpoint_dir = record_dir / 'checkpoints'
        checkpoint_dir.mkdir(parents=True, exist_ok=True)
        for path in archives:
            shutil.copy2(path, checkpoint_dir / path.name)
        adapter_name = f'{record["options"]["name"]}.safetensors'
        adapter_path = checkpoint_dir / adapter_name
        with zipfile.ZipFile(final) as archive:
            with archive.open(adapter_member) as source, adapter_path.open('wb') as target:
                shutil.copyfileobj(source, target)

        checkpoints = []
        for path in sorted(checkpoint_dir.glob('*.zip')):
            match = re.search(r'(\d+)', path.stem)
            checkpoint_step = int(match[1]) if match else expected_steps
            checkpoints.append(dict(name=path.name,
                                    label=f'{record["options"]["name"]} · {checkpoint_step} 步 checkpoint',
                                    id=f'{ident}:{path.stem}', sha256=sha256_file(path)))
        checkpoints.append(dict(name=adapter_name,
                                label=f'{record["options"]["name"]} · {step} 步 LoRA',
                                id=f'{ident}:{Path(adapter_name).stem}', sha256=sha256_file(adapter_path)))

        image_dir = record_dir / 'images'
        previews = []
        for path in sorted((output / 'preview').glob('*.png')):
            if path.is_file() and under(output / 'preview', path):
                image_dir.mkdir(parents=True, exist_ok=True)
                target = image_dir / path.name
                shutil.copy2(path, target)
                previews.append(dict(name=target.name, label=f'Iris-3B 训练预览 · {path.stem}', seed=None,
                                     sha256=sha256_file(target)))
        loss_plot = output / 'loss' / 'loss.html'
        recovered = dict(record, state='done', phase='已完成', ended=now(), checkpoints=checkpoints,
                         images=previews, mflux_output=str(output), recovered_output=True,
                         error=None)
        if loss_plot.is_file() and under(output, loss_plot):
            recovered['loss_plot'] = self.identity(loss_plot)
        write_json(record_dir / 'job.json', recovered)
        self.index_job(record_dir, recovered)
        return recovered

    def train_lora(self, job, options, trainer, model, images, model_id):
        python, code, cache = trainer
        profile = TRAINING_BASES[model_id]
        job.phase(f'检查 {self.training_backend.upper()} 训练环境')
        self.check_training_backend(job, python, code)
        dataset = job.directory / 'dataset.toml'
        dataset.write_text('[general]\ncaption_extension = ".txt"\n\n[[datasets]]\n'
                           f'resolution = [{profile["resolution"]}, {profile["resolution"]}]\n'
                           'batch_size = 1\nenable_bucket = true\n'
                           'min_bucket_reso = 256\nmax_bucket_reso = 1024\n\n[[datasets.subsets]]\n'
                           f'image_dir = {json.dumps(images.as_posix(), ensure_ascii=False)}\n'
                           f'num_repeats = {options["repeats"]}\n', encoding='utf-8')
        output = job.directory / 'checkpoints'
        output.mkdir()
        action = '续训 LoRA' if options.get('parent_lora') else '训练 LoRA'
        job.phase(f'{action} · {options["name"]}（{options["steps"]} 步）')
        optimizer = 'AdamW8bit' if self.training_backend == 'cuda' else 'AdamW'
        runner = Path(__file__).with_name('lora_train_runner.py')
        command = [python, '-m', 'accelerate.commands.launch', '--num_processes', '1', '--num_machines', '1',
                 '--dynamo_backend', 'no', '--mixed_precision', 'fp16', '--num_cpu_threads_per_process', '1',
                 runner]
        if model_id == 'sdxl':
            command.append('--sdxl')
        command += ['--pretrained_model_name_or_path', model,
                 '--dataset_config', dataset, '--output_dir', output, '--output_name', options['name'],
                 '--tokenizer_cache_dir', cache, '--network_module', 'networks.lora', '--network_train_unet_only',
                 '--network_dim', options['rank'], '--network_alpha', options['rank'],
                 '--max_train_steps', options['steps'], '--save_every_n_steps', options['save_every'],
                 '--learning_rate', options['lr'], '--unet_lr', options['lr'], '--lr_scheduler', 'cosine',
                 '--optimizer_type', optimizer, '--seed', options['seed'],
                 '--save_model_as', 'safetensors', '--mixed_precision', 'fp16', '--save_precision', 'fp16',
                 '--sdpa', '--cache_latents', '--gradient_checkpointing', '--max_data_loader_n_workers', '0']
        if options.get('parent_lora'):
            parent = self.loras().get(options['parent_lora'])
            if not parent or parent[3] != profile['family']:
                raise ValueError('续训 LoRA 已不存在或与底模系列不兼容。')
            command += ['--network_weights', parent[0]]
        if model_id == 'sd1':
            command += ['--clip_skip', '2']
        else:
            command += ['--cache_latents_to_disk', '--cache_text_encoder_outputs',
                        '--cache_text_encoder_outputs_to_disk']
        if ((self.training_backend == 'cuda' and model_id == 'sd1')
                or (self.training_backend == 'mps' and model_id == 'sdxl')):
            command.append('--lowram')
        job.update(command=[str(item) for item in command])
        environment = {'OMP_NUM_THREADS': '1', 'MKL_NUM_THREADS': '1', 'PYTHONFAULTHANDLER': '1',
                       'HF_HUB_OFFLINE': '1', 'HF_HOME': str(self.tools / 'hf-cache')}
        if self.training_backend == 'mps':
            environment['PYTORCH_ENABLE_MPS_FALLBACK'] = '1'
        job.run(command, cwd=code, env=environment)
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
