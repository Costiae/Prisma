"""Run native Ren'Py commands against a published project revision."""

import os
from pathlib import Path
import re
import shutil
from zipfile import ZIP_DEFLATED, ZipFile

from script_editor import project_path


PACKAGES = {'win': ('zip', '.zip'), 'linux': ('tar.bz2', '.tar.bz2'), 'mac': ('app-zip', '.zip'), 'web': ('zip', '.zip')}


def update_key(project):
    configured = os.environ.get('PRISMA_RENPY_UPDATE_KEYS', '').strip()
    if not configured or not isinstance(project, str) or not re.fullmatch(r'[a-f0-9]{16}', project):
        return None
    path = Path(configured).expanduser() / f'{project}.pem'
    return path if path.is_file() else None


def sdk_command():
    value = os.environ.get('PRISMA_RENPY_SDK', '').strip()
    if not value:
        return None
    root = Path(value).expanduser().resolve()
    if not (root / 'renpy.py').is_file():
        return None
    if os.name == 'nt':
        executable = root / 'lib' / 'py3-windows-x86_64' / 'python.exe'
        command = [executable, root / 'renpy.py']
    else:
        executable = root / 'renpy.sh'
        command = [executable]
    return (root, command) if executable.is_file() else None


class RenpySDK:
    def __init__(self, editor, pipeline, jobs):
        self.editor = editor
        self.pipeline = pipeline
        self.jobs = jobs

    @staticmethod
    def status(data):
        sdk = sdk_command()
        packages = [name for name in PACKAGES if name != 'web' or sdk and (sdk[0] / 'web').is_dir()]
        return {'available': sdk is not None, 'packages': packages, 'updates': update_key(data.get('project_id')) is not None}

    def start(self, data):
        sdk = sdk_command()
        if sdk is None:
            raise ValueError('请在启动 Prisma 时用 PRISMA_RENPY_SDK 指定 Ren’Py SDK 目录。')
        mode = data.get('mode')
        if mode not in ('lint', 'run', 'build', 'translate'):
            raise ValueError('请选择检查、实机运行、构建或生成翻译。')
        package = data.get('package')
        variant = data.get('variant', 'main')
        language = data.get('language')
        updates = data.get('updates', False)
        if mode == 'build' and (not isinstance(package, str) or package not in PACKAGES):
            raise ValueError('请选择受支持的发行目标。')
        if mode == 'translate' and (not isinstance(language, str) or not re.fullmatch(r'[a-z][a-z0-9_]{0,31}', language)):
            raise ValueError('语言标识须以小写字母开头，可用小写字母、数字和下划线。')
        root, command = sdk
        project = data.get('project_id')
        revision = data.get('revision')
        if type(updates) is not bool or updates and (mode != 'build' or package == 'web'):
            raise ValueError('当前构建目标不支持生成签名更新。')
        signing_key = update_key(project) if updates else None
        if updates and not signing_key:
            raise ValueError('此项目尚未配置 Ren’Py 更新签名密钥。')
        if mode == 'build' and package == 'web' and not (root / 'web').is_dir():
            raise ValueError('请先在 Ren’Py SDK 安装 Web 支持。')
        if not isinstance(revision, str) or not revision:
            raise ValueError('请先保存工程版本。')
        if mode != 'translate':
            entries = self.pipeline.inputs({'project_id': project, 'node': 'export'})['inputs']
            if not any(ref['kind'] == 'script' and ref['id'] == revision for entry in entries for ref in entry['references']):
                raise ValueError('请先通过要交给 SDK 的工程版本。')
        record = self.editor.open({'project_id': project, 'revision': revision})
        if mode == 'build':
            record = self.editor.variant(record, variant)
        env = {name: value for name, value in os.environ.items() if name.upper() in {
            'PATH', 'SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP', 'TMPDIR', 'HOME', 'USERPROFILE', 'APPDATA',
            'LOCALAPPDATA', 'DISPLAY', 'WAYLAND_DISPLAY', 'XDG_RUNTIME_DIR', 'LANG', 'LC_ALL',
        }}

        labels = {'lint': 'Ren’Py SDK 检查', 'run': 'Ren’Py 实机运行',
                  'build': 'Ren’Py 发行构建', 'translate': 'Ren’Py 生成翻译'}

        def work(job):
            directory = job.directory.resolve()
            target = directory / 'project'
            self.editor.materialize(project, target, record)
            job.phase(labels[mode])
            job.run([*command, '--version'], cwd=root, env=env, inherit_env=False)
            args = [*command, '--savedir', directory / 'saves']
            private_key = None
            if mode == 'build':
                output = directory / 'packages'
                output.mkdir()
                if package == 'web':
                    args.extend((root / 'launcher', 'web_build', '--destination', output / 'web', target))
                else:
                    args.extend((root / 'launcher', 'distribute', '--package', package,
                                 '--format', PACKAGES[package][0], '--destination', output, target))
                    if updates:
                        generated = target / 'game' / 'prisma_packaging_update.rpy'
                        with generated.open('x', encoding='utf-8') as stream:
                            stream.write('init 999 python:\n    build.include_update = True\n')
                        private_key = target / 'update.pem'
                    else:
                        args.append('--no-update')
            else:
                args.extend((target, mode))
            if mode == 'lint':
                args.append('--error-code')
            if mode == 'translate':
                args.extend((language, '--empty'))
            job.update(command=[str(part) for part in args])
            try:
                if private_key:
                    shutil.copyfile(signing_key, private_key)
                job.run(args, cwd=root, env=env, inherit_env=False)
            finally:
                if private_key:
                    private_key.unlink(missing_ok=True)
            if mode == 'build':
                packages = [{'name': path.name, 'size': path.stat().st_size} for path in sorted(output.iterdir())
                            if path.is_file() and path.name.endswith(PACKAGES[package][1])]
                if not packages:
                    raise ValueError('构建结束，但 SDK 未写出发行包。请查看任务日志。')
                if updates:
                    if not (output / 'updates.json').is_file() or not (output / 'updates.ecdsa').is_file():
                        raise ValueError('SDK 未生成签名更新清单。请查看项目的更新配置。')
                    update_archive = output / 'prisma-update.zip'
                    binary_packages = {item['name'] for item in packages}
                    update_files = [path for path in output.rglob('*')
                                    if path.is_file() and not path.is_symlink() and path.name not in binary_packages]
                    with ZipFile(update_archive, 'w', compression=ZIP_DEFLATED) as archive:
                        for path in sorted(update_files):
                            if path.resolve().is_relative_to(output) and path.name != 'update.pem':
                                archive.write(path, arcname=path.relative_to(output).as_posix())
                    packages.append({'name': update_archive.name, 'size': update_archive.stat().st_size})
                job.update(packages=packages)

        options = {'name': labels[mode], 'project_id': project, 'script_id': revision}
        if mode == 'build':
            options['package'] = package
            options['variant'] = variant
            options['updates'] = updates
        if mode == 'translate':
            options['language'] = language
        return self.jobs.start(f'renpy_{mode}', options, work)

    def _result(self, data, mode):
        project = data.get('project_id')
        self.editor.resources.project_record(project)
        ident = data.get('job_id', '')
        job = self.jobs.get(ident)
        if job['kind'] != f'renpy_{mode}' or job['state'] != 'done' or job['options'].get('project_id') != project:
            raise ValueError('本项目中找不到已完成的 Ren’Py 任务。')
        return job, self.jobs.directory(ident).resolve()

    def translations(self, data):
        job, directory = self._result(data, 'translate')
        game = directory / 'project' / 'game'
        root = game / 'tl' / job['options']['language']
        files = {}
        size = 0
        for path in sorted(root.rglob('*.rpy')):
            if not path.resolve().is_relative_to(game.resolve()) or not path.is_file():
                continue
            size += path.stat().st_size
            if len(files) >= 500 or size > 8_000_000:
                raise ValueError('生成的翻译过大，超出工程草稿的导入范围。')
            name = path.relative_to(game).as_posix()
            project_path(name)
            files[name] = path.read_text(encoding='utf-8-sig')
        return {'files': files, 'revision': job['options']['script_id'], 'language': job['options']['language']}

    def distribution(self, data, index):
        job, directory = self._result(data, 'build')
        packages = job.get('packages', [])
        if not 0 <= index < len(packages):
            raise ValueError('发行包不存在。')
        output = directory / 'packages'
        path = (output / packages[index]['name']).resolve()
        if not path.is_relative_to(output) or not path.is_file():
            raise ValueError('发行包文件不存在。')
        variant = job['options'].get('variant', 'main')
        attachment = (f'prisma-{variant}-update.zip' if path.name == 'prisma-update.zip'
                      else f"prisma-{variant}-{job['options']['package']}{PACKAGES[job['options']['package']][1]}")
        return path, attachment

    def builds(self, data):
        project = data.get('project_id')
        self.editor.resources.project_record(project)
        history = self.editor.library.list(kind='job', query='Ren’Py 发行构建', limit=30, project=project)
        items = []
        for row in history['items']:
            try:
                job = self.jobs.get(row['id'])
            except FileNotFoundError:
                continue
            if job['kind'] == 'renpy_build':
                items.append({key: job[key] for key in ('id', 'options', 'state', 'started')}
                             | {'packages': job.get('packages', [])})
        return {'items': items}
