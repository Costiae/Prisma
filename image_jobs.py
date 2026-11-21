"""Background jobs and their saved records for the image workspace."""

from collections.abc import Callable
from contextlib import contextmanager, nullcontext
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import threading
import uuid


ACTIVE = {'running', 'cancelling'}
JOB_ID = re.compile(r'[0-9a-f]{16}')
INDEX_PROJECTION = 3


class Cancelled(Exception):
    pass


class Busy(ValueError):
    pass


def now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def write_json(path: Path, value):
    temp = path.with_suffix('.part')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    temp.replace(path)


@contextmanager
def own_workspace(directory: Path):
    """Keep writes and GPU jobs in one service, even across different ports."""
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / '.service.lock').open('a+b') as lock:
        if os.name == 'nt':
            import msvcrt

            if lock.tell() == 0:
                lock.write(b'\0')
                lock.flush()
            lock.seek(0)
            acquire = lambda: msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            acquire = lambda: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            acquire()
        except OSError as error:
            raise Busy(f'已有 Prisma 服务使用此工作目录：{directory}。请让 WebUI 和 MCP 连接同一个服务。') from error
        yield


class Job:
    def __init__(self, directory: Path, kind: str, options: dict, index):
        self.directory = directory
        self.log = directory / 'job.log'
        self.index = index
        self.data = dict(id=directory.name, kind=kind, options=options, state='running',
                         started=now(), phase='正在准备', images=[], checkpoints=[])
        self.lock = threading.RLock()
        self.stop = threading.Event()
        self.process = None
        directory.mkdir(parents=True)
        self.update()

    def update(self, **fields):
        with self.lock:
            self.data.update(fields)
            write_json(self.directory / 'job.json', self.data)
            try:
                self.index(self.directory, self.data)
            except (OSError, sqlite3.Error, ValueError):
                logging.exception('Could not index job %s', self.directory.name)

    def check(self):
        if self.stop.is_set():
            raise Cancelled()

    def phase(self, title):
        self.check()
        self.log_event(title)
        self.update(phase=title, progress=None)

    def log_event(self, title):
        with self.log.open('ab') as stream:
            stream.write(f'[{now()}] {title}\n'.encode('utf-8'))

    def run(self, command, *, cwd=None, env=None, inherit_env=True):
        # API credentials belong to the server, not the local tools or installers.
        child_env = {k: v for k, v in os.environ.items() if k.upper() != 'OPENAI_API_KEY'} if inherit_env else {}
        child_env.update(PYTHONUTF8='1', PYTHONUNBUFFERED='1')
        child_env.update(env or {})
        with self.log.open('ab') as stream:
            with self.lock:
                self.check()
                process = self.process = subprocess.Popen(
                    [str(arg) for arg in command], cwd=cwd, env=child_env,
                    stdin=subprocess.DEVNULL, stdout=stream, stderr=subprocess.STDOUT,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
                )
            try:
                code = process.wait()
            finally:
                with self.lock:
                    self.process = None
        self.check()
        if code:
            raise ValueError(f'{Path(command[0]).name} 退出（代码 {code}）。请查看任务日志。')

    def cancel(self):
        with self.lock:
            if self.data['state'] not in ACTIVE:
                return
            self.update(state='cancelling', phase='正在取消')
            self.stop.set()
            process = self.process
            if process and process.poll() is None:
                if os.name == 'nt':
                    subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'],
                                   capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW,
                                   timeout=10, check=False)
                if process.poll() is None:
                    process.kill()


class Jobs:
    def __init__(self, root: Path, library, index):
        self.root = root
        self.library = library
        self.index = index
        self.lock = threading.Lock()
        self.active = None
        self.index_ready = False

    def start(self, kind: str, options: dict, work: Callable):
        with self.lock:
            if self.active is not None and self.active.data['state'] in ACTIVE:
                raise Busy('已有任务在运行；完成或取消后即可开始下一个。')
            job = self.active = Job(self.root / uuid.uuid4().hex[:16], kind, options, self.index)
            threading.Thread(target=self._run, args=(job, work), daemon=True).start()
            return self.get(job.directory.name)

    def _run(self, job, work):
        try:
            work(job)
            with job.lock:
                job.check()
                job.update(state='done', phase='已完成', ended=now())
        except Cancelled:
            job.update(state='cancelled', phase='已取消', ended=now())
        except (ValueError, OSError) as error:
            if job.stop.is_set():
                job.update(state='cancelled', phase='已取消', ended=now())
            else:
                job.update(state='failed', phase='任务失败', error=str(error), ended=now())
        except Exception:
            logging.exception('Image job %s failed', job.directory.name)
            job.update(state='failed', phase='任务失败', error='图像任务遇到内部错误。', ended=now())
        finally:
            with self.lock:
                if self.active is job:
                    self.active = None

    def directory(self, ident):
        if not JOB_ID.fullmatch(ident):
            raise FileNotFoundError('任务不存在。')
        directory = self.root / ident
        if not directory.resolve().is_relative_to(self.root.resolve()) or not (directory / 'job.json').is_file():
            raise FileNotFoundError('任务不存在。')
        return directory

    def get(self, ident, *, details=False):
        directory = self.directory(ident)
        active = self.active
        same_job = active is not None and active.directory == directory
        with active.lock if same_job else nullcontext():
            data = json.loads((directory / 'job.json').read_text(encoding='utf-8'))
        if data['state'] in ACTIVE and not same_job:
            data.update(state='interrupted', phase='服务重启，任务中断')
        if details:
            data['log'] = ''
            if (directory / 'job.log').is_file():
                with (directory / 'job.log').open('rb') as stream:
                    stream.seek(max(0, stream.seek(0, 2) - 6500))
                    log = stream.read().decode('utf-8', errors='replace')
                log = re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]', '', log)
                data['log'] = '\n'.join(log.splitlines()[-24:])
        return data

    def recent(self, *, project=None, shared=False):
        if not self.index_ready:
            self.refresh_index()
        result = []
        for row in self.library.list(kind='job', limit=24, project=project, shared=shared)['items']:
            try:
                job = self.get(row['id'])
            except (OSError, ValueError, KeyError):
                continue
            result.append({key: job[key] for key in ('id', 'kind', 'state', 'phase', 'started')})
        return result

    def refresh_index(self):
        indexed = self.library.owners('job:')
        skipped = 0
        for file in self.root.glob('*/job.json'):
            ident = file.parent.name
            if not JOB_ID.fullmatch(ident) or not file.resolve().is_relative_to(self.root.resolve()):
                continue
            owner = f'job:{ident}'
            try:
                active = self.active
                with active.lock if active and active.directory == file.parent else nullcontext():
                    stat = file.stat()
                    if indexed.get(owner) != [stat.st_mtime_ns, stat.st_size, INDEX_PROJECTION]:
                        data = json.loads(file.read_text(encoding='utf-8'))
                        if data['id'] != ident:
                            raise ValueError('Job ID does not match its directory.')
                        self.index(file.parent, data)
                indexed.pop(owner, None)
            except (OSError, ValueError, KeyError, TypeError):
                skipped += 1
        for owner in indexed:
            if not (self.root / owner.removeprefix('job:') / 'job.json').is_file():
                self.library.replace(owner, [])
        self.index_ready = True
        return skipped

    def cancel(self, ident):
        active = self.active
        if active and active.directory.name == ident:
            active.cancel()
        return self.get(ident)
