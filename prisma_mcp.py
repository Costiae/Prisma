"""Prisma tools on the same loopback service as the WebUI."""

import base64
import json
from pathlib import Path
import re
import secrets
import tempfile
from urllib.parse import quote

from image_toolkit import IMAGE_TYPES, ONLINE_MODELS, ONLINE_SIZES, SAMPLERS
from prisma_workflow import asset_prompt, draft, inspect, make_archive


VERSIONS = ('2025-11-25', '2025-06-18', '2025-03-26')


def schema(kind, **fields):
    return {'type': kind, **fields}


TEXT = schema('string')
INT = schema('integer')
BOOL = schema('boolean')
OBJECT = schema('object')
JOB = schema('string', pattern='^[0-9a-f]{16}$')


def enum(*options):
    return schema('string', enum=options)


def row(properties, required, *, extra=False):
    return schema('object', properties=properties, required=list(required), additionalProperties=extra)


DATASET_ITEM = row({'name': TEXT, 'caption': TEXT, 'data': schema('string', description='Optional base64 image. Otherwise read from source.')}, ('name',))
IMAGE_ASSET = row({'job_id': JOB, 'name': TEXT}, ('job_id', 'name'))


def rpc_error(ident, code, message):
    return {'jsonrpc': '2.0', 'id': ident, 'error': {'code': code, 'message': message}}


class PrismaMCP:
    def __init__(self, images, base_url):
        self.images = images
        self.base_url = base_url
        self.exports = images.home / 'exports'
        self.tools = {}
        tool = self.register
        tool('scenario_draft', 'Turn raw script text into editable Scenario and report unresolved lines.', draft,
             {'raw': TEXT, 'speakers': OBJECT, 'heading': TEXT}, ('raw',), read_only=True)
        tool('scenario_inspect', 'List scene IDs, speakers and numbered lines from a Scenario.', inspect,
             {'scenario': TEXT}, ('scenario',), read_only=True)
        tool('scenario_asset_prompt', 'Prepare a background, sprite or CG prompt and return the nearby Scenario lines for review.', asset_prompt,
             {'scenario': TEXT, 'scene': TEXT, 'kind': enum('background', 'sprite', 'cg'), 'line': INT,
              'brief': TEXT, 'trigger': TEXT}, ('scenario', 'scene', 'kind'), read_only=True)
        tool('image_catalog', 'List online readiness, local installation, reference sources, LoRAs, and recent jobs.',
             lambda _: images.catalog(), {}, read_only=True)
        tool('image_install', 'Download and validate missing local components. Inference installs the Windows Vulkan engine and SD 1.x base; training installs the CUDA or Apple MPS environment and selected SD 1.x or Animagine XL 4.0 anime SDXL base. On Apple Silicon, iris3b or anima installs MFlux and Apple MLX with the selected model snapshot for local LoRA training. Anima Base is anime-specific; its model and derivatives are limited to non-commercial use.',
             images.provision, {'target': enum('inference', 'training'), 'base_model': enum('sd1', 'sdxl', 'iris3b', 'anima')},
             ('target',), open_world=True)
        tool('dataset_source', 'List reference images and existing captions. Use source reference for the directory configured at startup.',
             self.dataset_source, {'source': TEXT}, ('source',), read_only=True)
        tool('dataset_image', 'Inspect a reference or saved training image for selection and captioning. Returns image content and its download URL.',
             self.dataset_image, {'source': TEXT, 'name': TEXT}, ('source', 'name'), read_only=True)
        tool('dataset_save', 'Save selected reference files and captions, or import PNG/JPEG/WebP as base64. Returns a new dataset ID and ZIP link.',
             self.save_dataset, {'name': TEXT, 'source': TEXT, 'trigger': TEXT,
                                 'items': schema('array', items=DATASET_ITEM, minItems=1, maxItems=150)},
             ('name', 'items'))
        tool('dataset_export', 'Get the dataset manifest, exact local directory, and URL for its ZIP of images and captions.',
             self.dataset_export, {'id': JOB}, ('id',), read_only=True)
        tool('dataset_restore', 'Restore an exported dataset with the same ID, names and hashes. Supply its ZIP as base64 or omit data to read the extracted snapshot from the configured reference directory.',
             images.restore_dataset, {'data': TEXT})
        tool('lora_import', 'Import a compatible SD 1.x safetensors LoRA as base64 (up to 45 MiB).',
             images.import_lora, {'name': TEXT, 'data': TEXT}, ('name', 'data'))
        tool('lora_train', 'Train an SD 1.x, SDXL, Apple Silicon Iris-3B, or anime-specific Anima Base LoRA on a saved dataset. Set parent_lora to continue a compatible SD 1.x or SDXL LoRA from its existing weights. Iris and Anima use MFlux on Apple MLX at 512 px with gradient checkpointing; Anima writes ComfyUI-compatible LoRA weights. Anima and its derivatives are limited to non-commercial use. Check image_job for checkpoints and provenance.',
             images.train, {'dataset': JOB, 'name': TEXT, 'base_model': enum('sd1', 'sdxl', 'iris3b', 'anima'),
                            'steps': INT, 'save_every': INT, 'rank': INT,
                            'lr': schema('number'), 'repeats': INT, 'seed': INT,
                            'parent_lora': TEXT}, ('dataset', 'name'))
        tool('image_generate', 'Queue local inference or an online API call. Online sends the prompt and uses account quota. Locally, use base_model anima, sdxl, or sd1 to choose a base without a LoRA; use [""] for the selected base or two compatible LoRA IDs for matched seeds. Anima runs with Apple MLX and accepts er_sde or euler sampling.',
             images.generate, {'mode': enum('local', 'online'), 'prompt': TEXT, 'count': INT,
                               'model': enum(*ONLINE_MODELS), 'size': enum(*ONLINE_SIZES),
                               'quality': enum('low', 'medium', 'high'), 'negative': TEXT,
                               'loras': schema('array', items=TEXT, minItems=1, maxItems=2),
                               'base_model': enum('sd1', 'sdxl', 'anima'), 'width': INT,
                               'height': INT, 'steps': INT, 'cfg': schema('number'), 'sampler': enum(*SAMPLERS),
                               'weight': schema('number'), 'seed': INT}, ('mode', 'prompt'), open_world=True)
        tool('image_job', 'Read a job state, parameters, input hashes, log tail, and artifact links by job ID.',
             self.job, {'id': JOB}, ('id',), read_only=True)
        tool('image_recover_training', 'Recover artifacts from a completed Iris-3B training run when MFlux wrote them to its timestamped output folder. The task is marked complete only after the final LoRA archive is found and registered.',
             self.recover_training, {'id': JOB}, ('id',))
        tool('image_cancel', 'Cancel a queued or running local image job using its ID. A submitted online request may already be billed.',
             lambda args: images.jobs.cancel(args['id']), {'id': JOB}, ('id',), destructive=True)
        tool('image_replay', 'Re-run an install, generation or training job. Recorded local inputs are checked before GPU work. Online replay makes a new paid request.',
             lambda args: images.replay(args['id']), {'id': JOB}, ('id',), open_world=True)
        tool('image_artifact', 'Get a candidate image or checkpoint path and URL. Set preview true to include the candidate image in the MCP result.',
             self.artifact, {'id': JOB, 'category': enum('images', 'checkpoints'), 'name': TEXT, 'preview': BOOL},
             ('id', 'category', 'name'), read_only=True)
        tool('renpy_export', 'Build a Ren’Py ZIP. Bind generated images by job ID and filename using image_assets. Ordinary files can be supplied as base64.',
             self.export, {'scenario': TEXT, 'cues': {'type': ['object', 'string']}, 'font': TEXT,
                           'image_assets': schema('object', additionalProperties=IMAGE_ASSET),
                           'files': schema('object', additionalProperties=TEXT)}, ('scenario',))

    def register(self, name, description, handler, fields, required=(), *, read_only=False, destructive=False, open_world=False):
        self.tools[name] = (handler, dict(name=name, description=description, inputSchema=row(fields, required),
                                         annotations=dict(readOnlyHint=read_only, destructiveHint=destructive,
                                                          openWorldHint=open_world)))

    def save_dataset(self, args):
        saved = self.images.save_dataset(args)
        saved['download'] = f'{self.base_url}/api/images/dataset/{saved["id"]}.zip'
        return saved

    def dataset_source(self, args):
        source = self.images.list_source(args['source'])
        for item in source['items']:
            item['url'] = self.base_url + item['url']
        if 'download' in source:
            source['download'] = self.base_url + source['download']
        return source

    def dataset_image(self, args):
        path = self.images.source_image(args['source'], args['name'])
        url = f'{self.base_url}/api/images/source/{quote(args["source"])}/{quote(path.name)}'
        return {'name': path.name, 'url': url}, self.preview(path)

    def dataset_export(self, args):
        ident = args['id']
        manifest = self.images.dataset_manifest(ident, verify=True)
        return {'manifest': manifest, 'path': str(self.images.dataset_root / ident),
                'download': f'{self.base_url}/api/images/dataset/{ident}.zip'}

    def job(self, args):
        record = self.images.jobs.get(args['id'], details=True)
        for category in ('images', 'checkpoints'):
            for artifact in record[category]:
                artifact['url'] = self.artifact_url(record['id'], category, artifact['name'])
        return record

    def recover_training(self, args):
        record = self.images.recover_iris_training(args['id'])
        for category in ('images', 'checkpoints'):
            for artifact in record[category]:
                artifact['url'] = self.artifact_url(record['id'], category, artifact['name'])
        return record

    def artifact_url(self, job, category, name):
        return f'{self.base_url}/api/images/files/{job}/{category}/{quote(name)}'

    def artifact(self, args):
        ident, category, name = args['id'], args['category'], args['name']
        path = self.images.artifact(ident, category, name)
        result = {**self.images.identity(path), 'path': str(path), 'url': self.artifact_url(ident, category, name)}
        return result, self.preview(path) if args.get('preview') and category == 'images' else []

    @staticmethod
    def preview(path):
        if path.stat().st_size > 12 * 1024 * 1024:
            raise ValueError('预览超过 12 MiB，请使用下载链接。')
        return [{'type': 'image', 'mimeType': IMAGE_TYPES[path.suffix.lower()],
                 'data': base64.b64encode(path.read_bytes()).decode('ascii')}]

    def export(self, args):
        ident = secrets.token_hex(8)
        self.exports.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='export-', dir=self.exports) as temporary:
            stage = Path(temporary)
            archive = make_archive(args, stage, self.images.artifact)
            out = stage / ident
            out.mkdir()
            archive.rename(out / archive.name)
            out.rename(self.exports / ident)
        return {'id': ident, 'path': str(self.exports / ident / 'prisma-renpy.zip'),
                'url': f'{self.base_url}/api/exports/{ident}/prisma-renpy.zip'}

    def call(self, params):
        name = params.get('name')
        args = params.get('arguments', {})
        if not isinstance(name, str) or name not in self.tools or not isinstance(args, dict):
            raise ValueError('未知工具或参数对象无效。')
        handler, tool = self.tools[name]
        spec = tool['inputSchema']
        missing = set(spec['required']) - args.keys()
        extra = args.keys() - spec['properties'].keys()
        if missing or extra:
            raise ValueError(f'缺少参数：{sorted(missing)}；未知参数：{sorted(extra)}。')
        for field, value in args.items():
            validate_input(value, spec['properties'][field], field)
        try:
            output = handler(args)
        except (OSError, ValueError) as error:
            return {'isError': True, 'content': [{'type': 'text', 'text': str(error)}]}
        data, blocks = output if isinstance(output, tuple) else (output, [])
        return {'structuredContent': data, 'content': [{'type': 'text', 'text': json.dumps(data, ensure_ascii=False)}, *blocks]}

    def handle(self, message):
        if not isinstance(message, dict) or message.get('jsonrpc') != '2.0' or not isinstance(message.get('method'), str):
            return rpc_error(None, -32600, 'Invalid Request')
        ident = message.get('id')
        if 'id' not in message:
            return None
        if type(ident) not in (int, str):
            return rpc_error(None, -32600, 'Invalid request ID')
        method = message['method']
        params = message.get('params', {})
        if not isinstance(params, dict):
            return rpc_error(ident, -32602, 'Invalid params')
        try:
            if method == 'initialize':
                version = params.get('protocolVersion')
                result = {'protocolVersion': version if version in VERSIONS else VERSIONS[0],
                          'capabilities': {'tools': {'listChanged': False}},
                          'serverInfo': {'name': 'prisma', 'version': '1.0.0'},
                          'instructions': 'Start with image_catalog. Use image_job to poll install, train, and generation jobs to completion.'}
            elif method == 'tools/list':
                result = {'tools': [entry for _, entry in self.tools.values()]}
            elif method == 'tools/call':
                result = self.call(params)
            elif method == 'ping':
                result = {}
            else:
                return rpc_error(ident, -32601, 'Method not found')
        except ValueError as error:
            return rpc_error(ident, -32602, str(error))
        return {'jsonrpc': '2.0', 'id': ident, 'result': result}


def validate_input(value, spec, field):
    types = {'string': (str,), 'boolean': (bool,), 'object': (dict,),
             'integer': (int,), 'number': (int, float), 'array': (list,)}
    kinds = spec['type'] if isinstance(spec['type'], list) else [spec['type']]
    allowed = tuple(item for kind in kinds for item in types[kind])
    if type(value) not in allowed or ('enum' in spec and value not in spec['enum']):
        raise ValueError(f'参数 {field} 的类型或值无效。')
    if 'pattern' in spec:
        if not re.fullmatch(spec['pattern'], value):
            raise ValueError(f'参数 {field} 的格式无效。')
