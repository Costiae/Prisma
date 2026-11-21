"""Prisma tools on the same loopback service as the WebUI."""

import base64
import json
from pathlib import Path
import re
import secrets
import tempfile
from urllib.parse import quote

from image_toolkit import IMAGE_TYPES, ONLINE_SIZES, SAMPLERS
from image_library import KINDS
from lora_export import save_export
from pipeline_workflow import NODES, REFERENCE_KINDS, PipelineWorkflow
from prisma_workflow import StudioWorkflow, inspect, make_archive
from provider_model_info import TEST_OPERATIONS
from script_editor import ScriptEditor
from script_story import inspect_story
from renpy_sdk import RenpySDK


VERSIONS = ('2025-11-25', '2025-06-18', '2025-03-26')


def schema(kind, **fields):
    return {'type': kind, **fields}


TEXT = schema('string')
INT = schema('integer')
BOOL = schema('boolean')
OBJECT = schema('object')
JOB = schema('string', pattern='^[0-9a-f]{16}$')


def row(properties, required, *, extra=False):
    return schema('object', properties=properties, required=list(required), additionalProperties=extra)


PROJECT_CONFIG = schema('object', additionalProperties=True)


def enum(*options):
    return schema('string', enum=options)


DATASET_ITEM = row({'name': TEXT, 'caption': TEXT, 'data': schema('string', description='Optional base64 image. Otherwise read from source.')}, ('name',))
IMAGE_ASSET = row({'job_id': JOB, 'name': TEXT}, ('job_id', 'name'))
SCENARIO = {'type': ['object', 'string'], 'description':
            'Scenario JSON v1: {version:1, chapters:[{id:"1", title, scenes:[{id:"1:1", lines:[{id, speaker, text}]}]}]}. '
            'Use null for a narrator; preserve line IDs on edits. Legacy Markdown text is accepted for import.'}
SCRIPT_INPUT = {'scenario': SCENARIO, 'cues': {'type': ['object', 'string']}, 'font': TEXT,
                    'asset_ids': schema('object', additionalProperties=TEXT), 'project_id': JOB,
                'config': PROJECT_CONFIG, 'project_title': TEXT}


def rpc_error(ident, code, message):
    return {'jsonrpc': '2.0', 'id': ident, 'error': {'code': code, 'message': message}}


class PrismaMCP:
    def __init__(self, images, base_url):
        self.images = images
        self.workflow = StudioWorkflow(images.resources)
        self.pipeline = PipelineWorkflow(images.resources)
        self.script_editor = ScriptEditor(images.resources, self.pipeline)
        self.renpy_sdk = RenpySDK(self.script_editor, self.pipeline, images.jobs)
        self.base_url = base_url
        self.exports = images.home / 'exports'
        self.tools = {}
        tool = self.register
        tool('scenario_draft', 'Turn raw script text into a Scenario JSON object with stable line IDs and original line references. For a project, use its global text LLM settings unless the Scenario task override is enabled.',
             self.workflow.draft, {'raw': TEXT, 'speakers': OBJECT, 'heading': TEXT, 'line_ids': BOOL,
                                  'project_id': JOB, 'config': PROJECT_CONFIG,
                                  'mode': enum('rules', 'openai-compatible'), 'parameters': OBJECT,
                                  'run_id': TEXT,
                                  'model_override': row({'provider_id': TEXT, 'model': TEXT}, ('provider_id', 'model'))}, ('raw',))
        tool('llm_status', 'Read whether this device has a saved API key for the supplied API host without revealing the secret.',
             self.workflow.llm_status, {'base_url': TEXT}, ('base_url',), read_only=True)
        tool('llm_credential', 'Save, clear, or check a machine-local API key. The secret is stored outside project data and never returned.',
             self.workflow.llm_credential, {'base_url': TEXT, 'action': enum('status', 'save', 'clear'), 'api_key': TEXT},
             ('base_url', 'action'))
        tool('llm_models', 'Fetch model IDs and metadata from an OpenAI-compatible API using credentials saved in the local Prisma database.',
             self.workflow.llm_api, {'base_url': TEXT, 'action': enum('models')}, ('base_url',), read_only=True)
        tool('llm_test', 'Test model connectivity with a short chat completion using credentials saved in the local Prisma database.',
             self.workflow.llm_api, {'base_url': TEXT, 'model': TEXT, 'action': enum('test')},
             ('base_url', 'model'))
        tool('api_providers', 'List workspace API provider profiles without revealing credentials.',
             self.workflow.providers.list, {}, read_only=True)
        tool('api_provider_save', 'Create or update a workspace API provider profile and optional local credential.',
             self.provider_save, {'id': TEXT, 'name': TEXT, 'protocol': enum('openai', 'anthropic'),
                                  'kind': TEXT, 'base_url': TEXT, 'api_key': TEXT}, ('name', 'base_url'))
        tool('api_provider_delete', 'Delete a workspace API provider profile.', self.provider_delete,
             {'id': TEXT}, ('id',))
        tool('api_provider_models', 'Fetch model IDs and metadata for a saved API provider.',
             self.provider_models, {'id': TEXT}, ('id',), open_world=True)
        tool('api_provider_test', 'Check a saved provider connection through its non-generation /models endpoint.',
             self.workflow.providers.test, {'id': TEXT}, ('id',), read_only=True, open_world=True)
        tool('api_provider_model_save', 'Declare a model manually or correct the use and metadata of a fetched model.',
             self.provider_model_save, {'id': TEXT, 'model': TEXT, 'name': TEXT,
                 'kind': enum('text', 'image', 'audio', ''), 'metadata': OBJECT, 'capabilities': OBJECT,
                 'probe': enum('', *TEST_OPERATIONS), 'test_voice': TEXT}, ('id', 'model', 'kind'))
        tool('api_provider_model_test', 'Send one small real request to a declared model and save the result. Model tests can use account quota.',
             self.workflow.providers.test_model, {'id': TEXT, 'model': TEXT}, ('id', 'model'), open_world=True)
        tool('api_model_defaults_save', 'Save workspace text, image and audio defaults. Each value is a provider_id/model object or null.',
             self.workflow.providers.save_defaults, {'defaults': OBJECT}, ('defaults',))
        tool('speech_generate', 'Generate Japanese dialogue with an OpenAI compatible speech model and register the audio as a project asset.',
             self.workflow.speech, {'project_id': JOB, 'text': TEXT, 'voice': TEXT, 'speed': schema('number'),
                                  'line_id': TEXT, 'run_id': TEXT, 'model_override': row({'provider_id': TEXT, 'model': TEXT}, ('provider_id', 'model'))},
             ('project_id', 'text', 'voice'), open_world=True)
        tool('generation_log', 'Read the latest Scenario or speech generation log, or find a specific run.',
             self.workflow.generation_log, {'project_id': JOB, 'node': enum('scenario', 'voice-assets'),
                                            'run_id': TEXT, 'include_response': BOOL}, ('node',), read_only=True)
        tool('scenario_inspect', 'Return normalized Scenario JSON and list scenes, speakers, and stable text item IDs.', inspect,
             {'scenario': SCENARIO}, ('scenario',), read_only=True)
        tool('scenario_save', 'Register edited source, Scenario and cues without changing their content.',
             self.workflow.save_documents, {'raw': TEXT, 'scenario': SCENARIO, 'cues': {'type': ['object', 'string']},
                                            'project_id': JOB})
        tool('scenario_anchor', 'Add stable IDs to text items, convert cues to by_line_id, and register both revisions.',
             self.workflow.anchor, {'scenario': SCENARIO, 'cues': {'type': ['object', 'string']}, 'project_id': JOB}, ('scenario',))
        tool('scenario_asset_prompt', 'Register a background, sprite or CG prompt with the nearby Scenario lines for review.', self.workflow.asset_prompt,
             {'scenario': SCENARIO, 'scene': TEXT, 'kind': enum('background', 'sprite', 'cg'), 'line': INT,
              'brief': TEXT, 'trigger': TEXT, 'project_id': JOB}, ('scenario', 'scene', 'kind'))
        tool('script_preview', 'Compile Ren’Py in memory with scene, dialogue and asset mappings.',
             self.workflow.preview_script, SCRIPT_INPUT, ('scenario',), read_only=True)
        tool('script_bind', 'Insert or replace one cue on a specific line and return the regenerated Ren’Py script.',
             self.workflow.bind_script, {**SCRIPT_INPUT, 'scene': TEXT, 'index': INT, 'line_id': TEXT,
                                         'cue': OBJECT, 'replace': INT}, ('scenario', 'scene', 'index', 'cue'))
        tool('script_editor_open', 'Read the latest editable Ren’Py draft or a prior revision in this project.',
             self.script_editor.open, {'project_id': JOB, 'revision': TEXT}, ('project_id',), read_only=True)
        project_sources = {key: schema('object', additionalProperties=TEXT)
                           for key in ('files', 'managed_assets', 'project_assets')}
        tool('script_editor_save', 'Save a Ren’Py project revision. files maps game-relative paths to text, project_assets maps paths to asset IDs. Legacy text updates script.rpy.',
             self.script_editor.save, {'project_id': JOB, 'base': TEXT, 'text': TEXT, **project_sources},
             ('project_id', 'base'))
        tool('script_editor_check', 'Check the project manifest, managed assets, entry label and duplicate labels across files.',
             self.script_editor.check, {'project_id': JOB, **project_sources}, ('project_id', 'files'), read_only=True)
        tool('script_editor_starter', 'Create a minimal runnable Ren’Py game manifest in memory.',
             self.script_editor.starter, {}, read_only=True)
        tool('script_editor_story', 'Inspect the scene list, characters and static branches across Ren’Py project files.',
             self.script_editor.story, {'files': project_sources['files'], 'file': TEXT, 'scene': TEXT}, ('files',), read_only=True)
        tool('script_editor_screens', 'Read or compile the visual screen model into portable Ren’Py screen source.',
             self.script_editor.screens, {'text': TEXT, 'screens': OBJECT}, read_only=True)
        tool('script_editor_variables', 'Read or compile default variables for visual widgets and Ren’Py source.',
             self.script_editor.variables, {'text': TEXT, 'variables': OBJECT}, read_only=True)
        tool('script_editor_history', 'List saved revisions in a project, newest first. Pass next as cursor to continue.',
             self.script_editor.history, {'project_id': JOB, 'cursor': TEXT}, ('project_id',), read_only=True)
        tool('script_editor_diff', 'Compare saved files and assets against a revision or its predecessor. Pass file to read its patch.',
             self.script_editor.diff, {'project_id': JOB, 'revision': TEXT, 'base': TEXT, 'file': TEXT},
             ('project_id', 'revision'), read_only=True)
        tool('script_editor_merge', 'Three-way merge a draft against the current saved revision without writing. Resolve reported keys with draft or latest.',
             self.script_editor.merge, {'project_id': JOB, 'base': TEXT, 'expected_head': TEXT,
                                        'resolutions': schema('object', additionalProperties=enum('draft', 'latest')),
                                        **project_sources}, ('project_id', 'base', 'files'), read_only=True)
        tool('script_editor_variants', 'Read or serialize named Ren’Py release variants. Each variant may cut at a scene and exclude specified project paths.',
             self.script_editor.variants, {'text': TEXT, 'variants': OBJECT, 'project_id': JOB, 'revision': TEXT}, read_only=True)
        tool('renpy_sdk_status', 'Check the local Ren’Py SDK and available build targets or project signing configuration.',
             self.renpy_sdk.status, {'project_id': JOB}, read_only=True)
        tool('renpy_sdk_start', 'Run native lint, play, build, or translation generation for trusted Ren’Py source. Build takes package; translate takes language. Read status and logs with image_job.',
             self.renpy_sdk.start, {'project_id': JOB, 'revision': TEXT, 'mode': enum('lint', 'run', 'build', 'translate'),
                                   'package': enum('win', 'linux', 'mac', 'web'), 'language': TEXT, 'variant': TEXT, 'updates': BOOL},
             ('project_id', 'revision', 'mode'))
        tool('renpy_sdk_translations', 'Read translation source files from a completed SDK job for script_editor_save.',
             self.renpy_sdk.translations, {'project_id': JOB, 'job_id': JOB}, ('project_id', 'job_id'), read_only=True)
        tool('renpy_sdk_builds', 'List recent project builds and artifacts with their local download URLs.',
             self.sdk_builds, {'project_id': JOB}, ('project_id',), read_only=True)
        tool('script_editor_asset', 'Return a portable path and a Ren’Py snippet for a current approved asset.',
             self.script_editor.prepare_asset, {'project_id': JOB, 'asset_id': TEXT,
                'action': enum('path', 'background', 'portrait', 'music', 'sound', 'voice')},
             ('project_id', 'asset_id', 'action'), read_only=True)
        tool('script_editor_localized_asset', 'Return the Ren’Py language-specific override path for an asset within this project.',
             self.script_editor.localized_asset, {'project_id': JOB, 'asset_id': TEXT, 'path': TEXT, 'language': TEXT},
             ('project_id', 'asset_id', 'path', 'language'), read_only=True)
        tool('script_story_inspect', 'Read the label outline, characters, and editable rows of one scene in a Ren’Py script.',
             inspect_story, {'text': TEXT, 'scene': TEXT}, ('text',), read_only=True)
        tool('script_story_edit', 'Apply a scene or row edit to Ren’Py text, preserving source outside the edited row. Use files and file for cross-file targets.',
             self.script_editor.edit_story, {'text': TEXT, 'scene': TEXT, 'project_id': JOB, 'line': INT, 'row': OBJECT,
                                             'files': project_sources['files'], 'file': TEXT,
                                             'operation': enum('add_scene', 'add_character', 'insert', 'replace', 'delete', 'move_up', 'move_down')},
             ('scene', 'operation'))
        tool('image_catalog', 'List online readiness, local installation, reference sources, LoRAs, and recent jobs.',
             lambda data: images.catalog(data.get('project_id')), {'project_id': JOB}, read_only=True)
        tool('image_api_credential', 'Save, clear, or check the OpenAI image API key in the local Prisma database; never returns the key.',
             images.image_api_credential,
             {'action': enum('status', 'save', 'clear'), 'api_key': TEXT}, ('action',))
        tool('image_api_models', 'Fetch available image-generation model IDs and metadata from OpenAI /v1/models, then cache them locally.',
             images.image_api_models, {}, read_only=True)
        tool('image_api_test', 'Test OpenAI image API connectivity using the non-billable /v1/models endpoint.',
             lambda _data: images.image_api_models({'action': 'test'}), {}, read_only=True)
        tool('library_list', 'Search the workspace catalog by category, label, ID, path, or recorded options. Returns paginated records.',
             images.library_list, {'kind': enum(*KINDS), 'query': TEXT, 'limit': INT, 'offset': INT,
                                   'project_id': JOB, 'shared': BOOL}, read_only=True)
        tool('library_item', 'Read a catalog record with metadata and related records, including source jobs, models, datasets, LoRAs, and exports.',
             images.library_item, {'kind': enum(*KINDS), 'id': TEXT}, ('kind', 'id'), read_only=True)
        tool('library_refresh', 'Reconcile the SQLite catalog with existing Prisma task manifests, datasets, model files, and exports.',
             images.refresh_library, {})
        tool('project_save', 'Save a new project or a new revision. Updating a project requires the latest revision ID.',
             images.resources.save_project, {'id': JOB, 'revision': TEXT, 'title': TEXT, 'raw': TEXT,
                'scenario': SCENARIO, 'cues': OBJECT, 'assets': schema('object', additionalProperties=TEXT),
                'font': TEXT, 'heading': TEXT, 'speakers': OBJECT, 'config': PROJECT_CONFIG}, ('title',))
        tool('project_open', 'Load a project and its current or requested revision. Includes Scenario, cues and reusable asset IDs.',
             self.open_project, {'id': JOB, 'revision': TEXT}, ('id',), read_only=True)
        tool('project_history', 'List the current project revision and its previous versions.',
             images.resources.project_history, {'id': JOB}, ('id',), read_only=True)
        tool('project_undo', 'Restore the previous project revision by writing it as a new revision.',
             images.resources.undo_project, {'id': JOB}, ('id',))
        tool('project_restore', 'Restore a selected historical project revision by writing it as a new revision.',
             images.resources.restore_project_revision, {'id': JOB, 'revision': TEXT}, ('id', 'revision'))
        tool('project_list', 'List active projects or projects in the trash.',
             images.resources.list_projects, {'trash': BOOL, 'offset': INT}, read_only=True)
        tool('project_delete', 'Move the project to the trash. Its revisions and files are retained.',
             lambda data: images.resources.set_project_trash(data, trash=True), {'id': JOB}, ('id',), destructive=True)
        tool('project_restore_deleted', 'Restore a project from the trash with its revisions and files.',
             lambda data: images.resources.set_project_trash(data, trash=False), {'id': JOB}, ('id',))
        stage = {'project_id': JOB, 'node': enum(*NODES)}
        tool('pipeline_state', 'Read the project graph and the current result version of each node.',
             self.pipeline.state, {'project_id': JOB}, ('project_id',), read_only=True)
        tool('pipeline_inputs', 'Read only the current input versions passed to this node by direct upstream nodes.',
             self.pipeline.inputs, stage, ('project_id', 'node'), read_only=True)
        tool('pipeline_approved', 'List current approved project outputs and their resource IDs. Specify a node to read its full output notes. needs_review indicates changed upstream versions.',
             self.pipeline.approved, stage, ('project_id',), read_only=True)
        tool('pipeline_reusable', 'Find available project and shared artifacts that the node can reuse.',
             self.pipeline.reusable, {**stage, 'query': TEXT}, ('project_id', 'node'), read_only=True)
        tool('pipeline_action', 'Record a node result version, retry a node, or pass its current output to every direct downstream node. Node versions belong to a single project.',
             self.pipeline.decide, {**stage, 'decision': enum('retry', 'pass'), 'base': TEXT,
                 'content': TEXT,
                 'references': schema('array', items=OBJECT), 'capture_project': BOOL,
                 'output': OBJECT, 'inputs': OBJECT}, ('project_id', 'node', 'decision', 'base'))
        tool('asset_import', 'Register media from base64, reuse an existing asset by library ID, or reuse a generated image.',
             images.import_workspace_asset, {'name': TEXT, 'data': TEXT, 'artifact': IMAGE_ASSET, 'library_id': TEXT,
                                              'project_id': JOB})
        tool('image_install', 'Download and validate missing local components. Inference installs the Windows Vulkan engine and SD 1.x base; training installs the CUDA or Apple MPS environment and selected SD 1.x or Animagine XL 4.0 anime SDXL base. On Apple Silicon, iris3b or anima installs MFlux and Apple MLX with the selected model snapshot for local LoRA training. Anima Base is anime-specific; its model and derivatives are limited to non-commercial use.',
             images.provision, {'target': enum('inference', 'training'), 'base_model': enum('sd1', 'sdxl', 'iris3b', 'anima')},
             ('target',), open_world=True)
        tool('dataset_source', 'List reference images and existing captions. Use source reference for the directory configured at startup.',
             self.dataset_source, {'source': TEXT}, ('source',), read_only=True)
        tool('dataset_image', 'Inspect a reference or saved training image for selection and captioning. Returns image content and its download URL.',
             self.dataset_image, {'source': TEXT, 'name': TEXT}, ('source', 'name'), read_only=True)
        tool('dataset_save', 'Save selected reference files and captions, or import PNG/JPEG/WebP as base64. Returns a new dataset ID and ZIP link.',
             self.save_dataset, {'name': TEXT, 'source': TEXT, 'trigger': TEXT, 'project_id': JOB,
                                 'items': schema('array', items=DATASET_ITEM, minItems=1, maxItems=150)},
             ('name', 'items'))
        tool('dataset_export', 'Export or reuse the dataset ZIP. Returns the manifest, local directory, and download URL.',
             self.dataset_export, {'id': JOB}, ('id',))
        tool('dataset_restore', 'Restore an exported dataset with the same ID, names and hashes. Supply its ZIP as base64 or omit data to read the extracted snapshot from the configured reference directory.',
             images.restore_dataset, {'data': TEXT, 'project_id': JOB})
        tool('lora_import', 'Import an existing safetensors LoRA as base64 (up to 45 MiB). Specify the matching base family.',
             images.import_lora, {'name': TEXT, 'data': TEXT, 'family': enum('sd1', 'sdxl', 'anima'), 'project_id': JOB}, ('name', 'data'))
        tool('lora_prepare', 'Select an existing LoRA for local generation and report whether its runtime is ready. Returns image_generate parameters.',
             images.prepare_lora, {'id': TEXT, 'project_id': JOB}, ('id',), read_only=True)
        tool('lora_export', 'Export selected LoRA IDs and their saved training datasets and records as a portable Prisma workspace ZIP. Base model dependencies are recorded in the manifest.',
             self.lora_export, {'loras': schema('array', items=TEXT, minItems=1), 'project_id': JOB}, ('loras',))
        tool('lora_train', 'Train an SD 1.x, SDXL, Apple Silicon Iris-3B, or anime-specific Anima Base LoRA on a saved dataset. Set parent_lora to continue a compatible SD 1.x or SDXL LoRA from its existing weights. Iris and Anima use MFlux on Apple MLX at 512 px with gradient checkpointing; Anima writes ComfyUI-compatible LoRA weights. Anima and its derivatives are limited to non-commercial use. Check image_job for checkpoints and provenance.',
             images.train, {'dataset': JOB, 'name': TEXT, 'base_model': enum('sd1', 'sdxl', 'iris3b', 'anima'),
                            'steps': INT, 'save_every': INT, 'rank': INT,
                            'lr': schema('number'), 'repeats': INT, 'seed': INT,
                            'parent_lora': TEXT, 'project_id': JOB}, ('dataset', 'name'))
        tool('image_generate', 'Queue local inference or an online API call. Online sends the prompt and uses account quota. Locally, use base_model anima, sdxl, or sd1 to choose a base without a LoRA; use [""] for the selected base or two compatible LoRA IDs for matched seeds. Anima runs with Apple MLX and accepts er_sde or euler sampling.',
             images.generate, {'mode': enum('local', 'online'), 'prompt': TEXT, 'count': INT,
                               'size': enum(*ONLINE_SIZES),
                               'model_override': row({'provider_id': TEXT, 'model': TEXT}, ('provider_id', 'model')),
                               'quality': enum('low', 'medium', 'high'), 'negative': TEXT,
                               'loras': schema('array', items=TEXT, minItems=1, maxItems=2),
                               'base_model': enum('sd1', 'sdxl', 'anima'), 'width': INT,
                               'height': INT, 'steps': INT, 'cfg': schema('number'), 'sampler': enum(*SAMPLERS),
                               'weight': schema('number'), 'seed': INT, 'prompt_id': TEXT,
                               'project_id': JOB}, ('mode', 'prompt'), open_world=True)
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
             self.export, {'scenario': SCENARIO, 'cues': {'type': ['object', 'string']}, 'font': TEXT,
                           'image_assets': schema('object', additionalProperties=IMAGE_ASSET),
                           'files': schema('object', additionalProperties=TEXT), 'config': PROJECT_CONFIG,
                           'project_title': TEXT, 'script_id': TEXT,
                           'asset_ids': schema('object', additionalProperties=TEXT), 'project_id': JOB})

    def provider_save(self, data):
        result = self.workflow.providers.save(data)
        self.images.index_models()
        return result

    def provider_delete(self, data):
        result = self.workflow.providers.delete(data)
        self.images.index_models()
        return result

    def provider_models(self, data):
        result = self.workflow.providers.models(data)
        self.images.index_models()
        return result

    def provider_model_save(self, data):
        result = self.workflow.providers.update_model(data)
        self.images.index_models()
        return result

    def register(self, name, description, handler, fields, required=(), *, read_only=False, destructive=False, open_world=False):
        self.tools[name] = (handler, dict(name=name, description=description, inputSchema=row(fields, required),
                                         annotations=dict(readOnlyHint=read_only, destructiveHint=destructive,
                                                          openWorldHint=open_world)))

    def save_dataset(self, args):
        saved = self.images.save_dataset(args)
        saved['download'] = f'{self.base_url}/api/images/dataset/{saved["id"]}.zip'
        return saved

    def sdk_builds(self, args):
        result = self.renpy_sdk.builds(args)
        for item in result['items']:
            if item['state'] != 'done':
                continue
            for index, package in enumerate(item['packages']):
                package['url'] = f'{self.base_url}/api/script/sdk/files/{item["id"]}/{index}?project_id={args["project_id"]}'
        return result

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
        archive = self.images.dataset_export_path(ident, manifest=manifest)
        return {'manifest': manifest, 'path': str(self.images.dataset_root / ident), 'archive': str(archive),
                'download': f'{self.base_url}/api/images/dataset/{ident}.zip'}

    def open_project(self, args):
        self.images.ensure_library()
        return self.images.resources.load_project(args)

    def lora_export(self, args):
        result = save_export(self.images, args)
        return dict(result, url=f'{self.base_url}{result["url"]}')

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
        links = []
        approved_script = None
        if project := args.get('project_id'):
            links.append(('for_project', 'project', project))
            settings = self.images.resources.load_project({'id': project})
            args.setdefault('config', settings.get('config'))
            args.setdefault('project_title', settings['title'])
        if script_id := args.get('script_id'):
            accepted = self.pipeline.inputs({'project_id': project, 'node': 'export'})['inputs']
            if not any(ref['kind'] == 'script' and ref['id'] == script_id
                       for entry in accepted for ref in entry['references']):
                raise ValueError('请先从「脚本生成」通过当前版本，再导出工程。')
            approved_script = self.images.library.get('script', script_id)['metadata']
            links.append(('from_script', 'script', script_id))
        self.exports.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='export-', dir=self.exports) as temporary:
            stage = Path(temporary)
            if approved_script is not None and 'managed_assets' in approved_script:
                archive = self.script_editor.make_archive(project, stage, approved_script)
                assets = set(approved_script['managed_assets'].values()) | set(approved_script.get('project_assets', {}).values())
                links.extend(('uses_asset', 'asset', ident) for ident in sorted(assets))
            else:
                if approved_script is not None and args.get('scenario') is None:
                    args.update(scenario=settings['scenario'], cues=settings['cues'],
                                font=settings['font'], asset_ids=settings['assets'])
                archive = make_archive(args, stage, self.images.artifact, resources=self.images.resources,
                                       links=links, approved_script=approved_script and approved_script['text'])
            out = stage / ident
            out.mkdir()
            archive.rename(out / archive.name)
            out.rename(self.exports / ident)
        path = self.exports / ident / 'prisma-renpy.zip'
        self.images.record_export(ident, path, 'renpy', links=links)
        return {'id': ident, 'path': str(path),
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
            if args.get('project_id'):
                self.images.resources.project_record(args['project_id'])
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
