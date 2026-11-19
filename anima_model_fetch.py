"""Download a pinned Anima Base snapshot and tokenizer files for Prisma/MFlux."""

import argparse
import hashlib
import json
from pathlib import Path

from huggingface_hub import HfApi, snapshot_download


MODEL_REPO = 'circlestone-labs/Anima'
MODEL_REVISION = '457fbf842cb86e96af72c65bdd13e3f1c448de84'
MODEL_FILES = [
    'README.md', 'LICENSE.md',
    'split_files/diffusion_models/anima-base-v1.0.safetensors',
    'split_files/text_encoders/qwen_3_06b_base.safetensors',
    'split_files/vae/qwen_image_vae.safetensors',
]
TOKENIZERS = {
    'Qwen/Qwen3-0.6B-Base': {
        'directory': 'tokenizer',
        'patterns': ['tokenizer.json', 'tokenizer_config.json'],
    },
    'google/t5-v1_1-xxl': {
        'directory': 'tokenizer_2',
        'patterns': ['spiece.model', 'tokenizer_config.json', 'special_tokens_map.json'],
    },
}


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def model_info(api, repo, revision):
    return api.model_info(repo, revision=revision).sha


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model-dir', type=Path, required=True)
    parser.add_argument('--cache-dir', type=Path, required=True)
    parser.add_argument('--metadata', type=Path, required=True)
    options = parser.parse_args()

    api = HfApi(token=False)
    resolved_model_revision = model_info(api, MODEL_REPO, MODEL_REVISION)
    model_dir = options.model_dir.expanduser().resolve()
    cache_dir = options.cache_dir.expanduser().resolve()
    model_snapshot = Path(snapshot_download(
        repo_id=MODEL_REPO,
        revision=resolved_model_revision,
        cache_dir=cache_dir,
        allow_patterns=MODEL_FILES,
        token=False,
    ))
    model_dir.mkdir(parents=True, exist_ok=True)
    model_files = [model_dir / name for name in MODEL_FILES]
    for relative, path in zip(MODEL_FILES, model_files):
        source = model_snapshot / relative
        if not source.is_file():
            raise FileNotFoundError(f'Anima snapshot is incomplete: {source.name}')
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.is_symlink() or path.exists():
            path.unlink()
        path.symlink_to(source.resolve())

    tokenizer_records = {}
    for repo, spec in TOKENIZERS.items():
        revision = model_info(api, repo, 'main')
        target_dir = model_dir / spec['directory']
        snapshot = Path(snapshot_download(
            repo_id=repo,
            revision=revision,
            cache_dir=cache_dir,
            allow_patterns=spec['patterns'],
            token=False,
        ))
        target_dir.mkdir(parents=True, exist_ok=True)
        files = [target_dir / name for name in spec['patterns']]
        for name, path in zip(spec['patterns'], files):
            source = snapshot / name
            if not source.is_file():
                raise FileNotFoundError(f'Tokenizer snapshot is incomplete: {repo}')
            if path.is_symlink() or path.exists():
                path.unlink()
            path.symlink_to(source.resolve())
        tokenizer_records[repo] = {
            'revision': revision,
            'files': [{'path': str(path), 'sha256': sha256(path)} for path in files],
        }

    record = {
        'model_repo': MODEL_REPO,
        'model_revision': resolved_model_revision,
        'model_license': 'CircleStone Labs Non-Commercial License',
        'model_path': str(model_dir),
        'files': [{'path': str(path), 'sha256': sha256(path)} for path in model_files],
        'tokenizers': tokenizer_records,
    }
    options.metadata.parent.mkdir(parents=True, exist_ok=True)
    options.metadata.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'model_revision': resolved_model_revision,
                      'files': len(model_files), 'tokenizer_repositories': len(tokenizer_records)},
                     ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
