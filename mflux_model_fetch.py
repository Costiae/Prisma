"""Download pinned public model snapshots for Prisma's MFlux trainer."""

import argparse
import json
from pathlib import Path

from huggingface_hub import HfApi, snapshot_download


MODEL_REPO = 'speridlabs/iris-3b'
TEXT_ENCODER_REPO = 'Qwen/Qwen3-VL-4B-Instruct'
TEXT_ENCODER_REVISION = 'ebb281ec70b05090aa6165b016eac8ec08e71b17'
MODEL_FILES = ['README.md', 'config.yaml', 'model.safetensors']
TEXT_ENCODER_FILES = [
    'config.json', 'generation_config.json', 'model.safetensors.index.json', 'model-*.safetensors',
    'tokenizer.json', 'tokenizer_config.json', 'special_tokens_map.json', 'chat_template.json',
    'merges.txt', 'vocab.json', 'preprocessor_config.json', 'video_preprocessor_config.json',
]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model-dir', type=Path, required=True)
    parser.add_argument('--cache-dir', type=Path, required=True)
    parser.add_argument('--metadata', type=Path, required=True)
    options = parser.parse_args()

    api = HfApi(token=False)
    model_revision = api.model_info(MODEL_REPO).sha
    model_path = Path(snapshot_download(
        repo_id=MODEL_REPO,
        revision=model_revision,
        local_dir=options.model_dir,
        cache_dir=options.cache_dir,
        allow_patterns=MODEL_FILES,
        token=False,
    ))
    # MFlux loads upstream Iris checkpoints through the standard HF snapshot cache.
    # `local_dir` downloads are convenient for Prisma's model inventory, but they are
    # not a valid `model_path` for MFlux (that argument means an mflux-save directory).
    # Register the already-downloaded files as the pinned repo snapshot without copying
    # the 12 GB weight file a second time.
    model_snapshot = (options.cache_dir / 'models--speridlabs--iris-3b' / 'snapshots' / model_revision)
    model_snapshot.mkdir(parents=True, exist_ok=True)
    for name in MODEL_FILES:
        source = model_path / name
        if not source.is_file():
            continue
        target = model_snapshot / name
        if target.is_symlink() and target.resolve() == source.resolve():
            continue
        if target.exists() or target.is_symlink():
            target.unlink()
        target.symlink_to(source.resolve())
    model_refs = options.cache_dir / 'models--speridlabs--iris-3b' / 'refs'
    model_refs.mkdir(parents=True, exist_ok=True)
    (model_refs / 'main').write_text(model_revision, encoding='utf-8')
    text_encoder_path = Path(snapshot_download(
        repo_id=TEXT_ENCODER_REPO,
        revision=TEXT_ENCODER_REVISION,
        cache_dir=options.cache_dir,
        allow_patterns=TEXT_ENCODER_FILES,
        token=False,
    ))
    refs = (options.cache_dir / 'models--Qwen--Qwen3-VL-4B-Instruct' / 'refs')
    refs.mkdir(parents=True, exist_ok=True)
    (refs / 'main').write_text(TEXT_ENCODER_REVISION, encoding='utf-8')

    record = {
        'model_repo': MODEL_REPO,
        'model_revision': model_revision,
        'model_path': str(model_path),
        'model_license': 'Apache-2.0',
        'text_encoder': {
            'repo': TEXT_ENCODER_REPO,
            'revision': TEXT_ENCODER_REVISION,
            'snapshot': str(text_encoder_path),
            'files': [str(path) for path in sorted(text_encoder_path.iterdir())
                      if path.is_file() and (path.suffix == '.safetensors' or path.name in {
                          'config.json', 'model.safetensors.index.json', 'tokenizer.json',
                          'tokenizer_config.json', 'special_tokens_map.json', 'chat_template.json',
                          'merges.txt', 'vocab.json', 'preprocessor_config.json', 'video_preprocessor_config.json',
                      })],
        },
    }
    if not (model_path / 'model.safetensors').is_file() or not (model_path / 'config.yaml').is_file():
        raise FileNotFoundError('Iris-3B snapshot is incomplete.')
    options.metadata.parent.mkdir(parents=True, exist_ok=True)
    options.metadata.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'model_revision': model_revision, 'text_encoder_revision': TEXT_ENCODER_REVISION,
                      'model_path': str(model_path), 'text_encoder_files': len(record['text_encoder']['files'])},
                     ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
