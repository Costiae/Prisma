"""Export selected LoRAs and their training inputs into a Prisma workspace ZIP."""

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import secrets
import tempfile
import zipfile

from image_jobs import JOB_ID
from image_toolkit import (IMAGE_TYPES, TRAINING_BASES, ImageToolkit, child_file,
                           sha256_file, under)


ARCHIVE_NAME = 'prisma-lora.zip'


def write_json(archive, name, data):
    archive.writestr(name, json.dumps(data, ensure_ascii=False, indent=2) + '\n')


def legacy_dataset(images, recipe):
    source = recipe['source']
    location = images.sources().get(source)
    if not location:
        raise ValueError(f'找不到旧训练数据来源：{source}')
    folder, _ = location
    items = []
    for name in recipe['images']:
        path = child_file(folder, name)
        if path.suffix.lower() not in IMAGE_TYPES:
            raise ValueError(f'旧数据集含不支持的图片格式：{name}')
        caption = child_file(folder, path.with_suffix('.txt').name)
        items.append(dict(name=name, original=name,
                          caption=caption.read_text(encoding='utf-8').strip(),
                          sha256=sha256_file(path)))
    manifest = dict(name=recipe['name'], trigger=recipe['trigger'], source=source, items=items)
    ident = hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()[:16]
    return folder, dict(manifest, id=ident)


def write_dataset(archive, folder, manifest):
    prefix = f'datasets/{manifest["id"]}'
    write_json(archive, f'{prefix}/dataset.json', {key: value for key, value in manifest.items() if key != 'project_id'})
    for item in manifest['items']:
        for name in (item['name'], Path(item['name']).with_suffix('.txt').name):
            archive.write(child_file(folder, name), f'{prefix}/images/{name}')


def write_notes(archive, root, notes):
    for name, relative in notes.items():
        destination = PurePosixPath(name)
        source = root / relative
        if (not destination.parts or destination.is_absolute() or '..' in destination.parts
                or '\\' in name or ':' in name or not under(root, source) or not source.is_file()):
            raise ValueError(f'迁移附录路径无效：{name}')
        archive.write(source, f'notes/{destination.as_posix()}')


def export_archive(images, ids, target):
    """Build an archive only on request. The extracted directory is an image workspace."""
    if not isinstance(ids, list) or not ids or any(not isinstance(ident, str) for ident in ids):
        raise ValueError('请选择要导出的 LoRA。')
    catalog = images.loras()
    missing = [ident for ident in ids if ident not in catalog]
    if missing:
        raise ValueError(f'LoRA 不存在：{", ".join(missing)}')
    if len(set(ids)) != len(ids):
        raise ValueError('请选择不同的 LoRA。')
    recipe_path = images.home / 'lora-transfer.json'
    recipe = json.loads(recipe_path.read_text(encoding='utf-8')) if recipe_path.is_file() else {}
    recipes = recipe.get('legacy', {})
    jobs, datasets, weights = {}, {}, {}
    entries = []
    for ident in ids:
        path, label, weight, family = catalog[ident]
        digest = sha256_file(path)
        job_id = ident.split(':', 1)[0]
        entry = dict(id=ident, label=label, strength=weight, family=family,
                     sha256=digest, size=path.stat().st_size)
        if JOB_ID.fullmatch(job_id):
            if job_id not in jobs:
                job = images.jobs.get(job_id)
                jobs[job_id] = (job, [])
                dataset = job['options']['dataset']
                if dataset not in datasets:
                    datasets[dataset] = (images.dataset_root / dataset / 'images',
                                         images.dataset_manifest(dataset, verify=True))
            job, selected = jobs[job_id]
            item = next((item for item in job['checkpoints'] if item['name'] == path.name), None)
            if not item or item.get('sha256', digest) != digest:
                raise ValueError(f'LoRA 校验失败：{path.name}')
            selected.append(item)
            entry.update(workspace_id=ident, dataset_id=job['options']['dataset'],
                         path=f'jobs/{job_id}/checkpoints/{path.name}')
        else:
            prefix = '' if family == 'sd1' else f'{family}:'
            folder = 'loras' if family == 'sd1' else f'loras/{family}'
            entry.update(workspace_id=f'import:{prefix}{path.stem}', path=f'{folder}/{path.name}')
            branch = ident.split('-', 1)[0]
            if branch in recipes:
                if branch not in datasets:
                    datasets[branch] = legacy_dataset(images, recipes[branch])
                entry.update(dataset_id=datasets[branch][1]['id'], training=recipes[branch]['training'])
        key = entry['path'].casefold()
        if key in weights:
            if weights[key][1] != digest:
                raise ValueError(f'导出包含同名的不同权重：{path.name}')
            entry['path'] = weights[key][2]
        else:
            weights[key] = (path, digest, entry['path'])
        entries.append(entry)

    bases = {ident: {key: data[key] for key in ('label', 'family', 'name', 'revision', 'url', 'sha256')
                     if key in data}
             for ident, data in TRAINING_BASES.items()
             if any(entry['family'] == data['family'] for entry in entries)}
    with target.open('xb') as stream:
        try:
            with zipfile.ZipFile(stream, 'w', compression=zipfile.ZIP_STORED) as archive:
                write_json(archive, 'manifest.json', dict(format=1, bases=bases, loras=entries))
                for path, _, name in weights.values():
                    archive.write(path, name)
                for folder, manifest in datasets.values():
                    write_dataset(archive, folder, manifest)
                for job_id, (job, selected) in jobs.items():
                    options = {key: value for key, value in job['options'].items() if key != 'project_id'}
                    exported = dict(job, options=options, checkpoints=selected, images=[])
                    if isinstance((job.get('inputs') or {}).get('dataset'), dict):
                        inputs = job['inputs'].copy()
                        inputs['dataset'] = {key: value for key, value in inputs['dataset'].items() if key != 'project_id'}
                        exported['inputs'] = inputs
                    write_json(archive, f'jobs/{job_id}/job.json', exported)
                    directory = images.jobs.directory(job_id)
                    for name in ('runtime.json', 'dataset.toml'):
                        if (directory / name).is_file():
                            archive.write(child_file(directory, name), f'jobs/{job_id}/{name}')
                write_notes(archive, images.root, recipe.get('notes', {}))
        except BaseException:
            stream.close()
            target.unlink()
            raise
    return dict(loras=len(entries), datasets=len(datasets))


def save_export(images, data):
    exports = images.home / 'exports'
    exports.mkdir(parents=True, exist_ok=True)
    ident = secrets.token_hex(8)
    with tempfile.TemporaryDirectory(dir=exports, prefix='lora-export-') as temporary:
        stage = Path(temporary) / ident
        stage.mkdir()
        counts = export_archive(images, data.get('loras'), stage / ARCHIVE_NAME)
        stage.rename(exports / ident)
    path = exports / ident / ARCHIVE_NAME
    links = [('includes', 'lora', item) for item in data['loras']]
    links.extend(images.resources.ownership(data.get('project_id')))
    images.record_export(ident, path, 'lora', links=links, metadata=counts)
    return dict(id=ident, path=str(path),
                url=f'/api/exports/{ident}/{ARCHIVE_NAME}', **counts)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    parser.add_argument('--image-workspace', type=Path)
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument('--lora', action='append', help='Catalog LoRA ID. Repeat to include multiple checkpoints.')
    selection.add_argument('--all', action='store_true', help='Include all checkpoints visible in the catalog.')
    args = parser.parse_args()
    images = ImageToolkit(Path(__file__).resolve().parent, workspace=args.image_workspace)
    ids = list(images.loras()) if args.all else args.lora
    try:
        counts = export_archive(images, ids, args.output)
        images.record_export(secrets.token_hex(8), args.output.resolve(), 'lora',
                             links=[('includes', 'lora', item) for item in ids], metadata=counts)
    except (OSError, ValueError, KeyError) as error:
        parser.exit(1, f'{error}\n')
    print(f'{args.output.resolve()} ({counts["loras"]} LoRAs, {counts["datasets"]} datasets)')


if __name__ == '__main__':
    main()
