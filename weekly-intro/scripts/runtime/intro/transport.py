"""Isolated native AE processes. Never dispatch scripts to the user's AE."""
import ctypes
from ctypes import wintypes
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import tempfile
import time


class AEError(RuntimeError):
    pass


def save_json(path, value):
    path = Path(path)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(path)


def sha256(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def ae_command(executable, driver):
    return [str(executable), '-m', '-r', str(driver)]


def render_command(executable, project, queue_index):
    if int(queue_index) < 1:
        raise ValueError('queue_index must be positive')
    return [str(executable), '-project', str(project), '-rqindex', str(int(queue_index)), '-v', 'ERRORS_AND_PROGRESS']


def claim_dispatch(folder, operation, receipt):
    path = Path(folder) / (operation + '-dispatch.json')
    with path.open('x', encoding='utf-8') as stream:
        json.dump(dict(receipt, operation=operation, status='submitted'), stream, ensure_ascii=False, indent=2)
    return path


def validate_result(result, token, operation, project):
    if result.get('token') != token or result.get('operation') != operation:
        raise AEError('Worker result identity mismatch; no retry was sent')
    if result.get('status') != 'ok':
        raise AEError(result.get('error', 'AE worker failed'))
    if project is not None and Path(result.get('project_path', '')).resolve() != Path(project).resolve():
        raise AEError('Worker did not operate on the expected project copy')
    return result


class OwnedJob:
    """Windows Job Object: cleanup can only affect processes owned by this launch."""
    def __init__(self):
        self.kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        self.kernel.CreateJobObjectW.restype = wintypes.HANDLE
        self.kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        self.kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        self.kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        self.kernel.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]

        class Basic(ctypes.Structure):
            _fields_ = [('process_time', ctypes.c_int64), ('job_time', ctypes.c_int64), ('flags', wintypes.DWORD),
                        ('minimum', ctypes.c_size_t), ('maximum', ctypes.c_size_t), ('active', wintypes.DWORD),
                        ('affinity', ctypes.c_size_t), ('priority', wintypes.DWORD), ('scheduling', wintypes.DWORD)]

        class IO(ctypes.Structure):
            _fields_ = [(name, ctypes.c_uint64) for name in ('reads', 'writes', 'other', 'read_bytes', 'write_bytes', 'other_bytes')]

        class Extended(ctypes.Structure):
            _fields_ = [('basic', Basic), ('io', IO), ('process_memory', ctypes.c_size_t), ('job_memory', ctypes.c_size_t),
                        ('peak_process', ctypes.c_size_t), ('peak_job', ctypes.c_size_t)]

        self.handle = self.kernel.CreateJobObjectW(None, None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        limits = Extended()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self.kernel.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            self.close()
            raise ctypes.WinError(ctypes.get_last_error())

    def assign(self, process):
        if not self.kernel.AssignProcessToJobObject(self.handle, wintypes.HANDLE(int(process._handle))):
            process.terminate()
            raise AEError('Could not isolate AE in an owned process job')

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None


def _launch(command, log_stream, env, job):
    startup = subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = 0
    process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=log_stream, stderr=subprocess.STDOUT,
                               env=env, startupinfo=startup, creationflags=subprocess.CREATE_NO_WINDOW)
    job.assign(process)
    return process


def run_script(folder, operation, payload, config, timeout=300):
    if os.name != 'nt':
        raise AEError('Native After Effects requires Windows')
    if not 0 < float(timeout) <= 3600:
        raise ValueError('Script timeout must be in (0, 3600] seconds')
    folder = Path(folder).resolve()
    folder.mkdir(parents=True, exist_ok=True)
    token = secrets.token_hex(24)
    bridge = Path(tempfile.mkdtemp(prefix='weekly-intro-'))
    result_path = bridge / 'result.json'
    worker = Path(__file__).resolve().parents[1] / 'ae' / 'worker.jsx'
    job_data = dict(payload, token=token, operation=operation, result_path=str(result_path))
    driver = bridge / 'driver.jsx'
    driver.write_text('var JOB = ' + json.dumps(job_data, ensure_ascii=True) + ';\n' + worker.read_text(encoding='utf-8'), encoding='utf-8')
    executable = Path(config['ae']).resolve(strict=True)
    if executable.suffix.lower() == '.exe' and executable.with_suffix('.com').is_file():
        executable = executable.with_suffix('.com')
    command = ae_command(executable, driver)
    receipt = {'token': token, 'command': command, 'bridge_dir': str(bridge), 'submitted_at': time.time()}
    receipt_path = claim_dispatch(folder, operation, receipt)
    env = dict(os.environ, WEEKLY_INTRO_RUN_TOKEN=token)
    job = OwnedJob()
    try:
        with (folder / (operation + '.log')).open('wb') as log:
            process = _launch(command, log, env, job)
            receipt.update(pid=process.pid, status='running')
            save_json(receipt_path, receipt)
            deadline = time.monotonic() + timeout
            while not result_path.is_file():
                if time.monotonic() >= deadline:
                    raise TimeoutError('AE script timed out; inspect logs/status before a new run')
                if process.poll() not in (None, 0):
                    raise AEError('AE launcher failed with exit code ' + str(process.returncode))
                time.sleep(0.25)
            result = json.loads(result_path.read_text(encoding='utf-8-sig'))
            expected_project = payload.get('output_aep', payload.get('project'))
            validate_result(result, token, operation, expected_project)
            save_json(folder / (operation + '-result.json'), result)
            receipt.update(status='completed', finished_at=time.time())
            save_json(receipt_path, receipt)
            return result
    except Exception as error:
        receipt.update(status='timed_out' if isinstance(error, TimeoutError) else 'failed', error=str(error), finished_at=time.time())
        if result_path.is_file():
            receipt['worker_result'] = json.loads(result_path.read_text(encoding='utf-8-sig'))
        save_json(receipt_path, receipt)
        raise
    finally:
        job.close()


def run_render(folder, project, queue_index, config, timeout=1800):
    if os.name != 'nt':
        raise AEError('Native After Effects requires Windows')
    if not 0 < float(timeout) <= 86400:
        raise ValueError('Render timeout must be in (0, 86400] seconds')
    folder = Path(folder).resolve()
    command = render_command(str(Path(config['aerender']).resolve(strict=True)), str(Path(project).resolve(strict=True)), queue_index)
    receipt = {'command': command, 'queue_index': queue_index, 'submitted_at': time.time()}
    receipt_path = claim_dispatch(folder, 'render', receipt)
    job = OwnedJob()
    try:
        with (folder / 'render.log').open('wb') as log:
            process = _launch(command, log, os.environ.copy(), job)
            receipt.update(pid=process.pid, status='running')
            save_json(receipt_path, receipt)
            code = process.wait(timeout=timeout)
            if code != 0:
                raise AEError('aerender failed with exit code ' + str(code))
        receipt.update(status='rendered', exit_code=code, finished_at=time.time())
        save_json(receipt_path, receipt)
        return receipt
    except Exception as error:
        receipt.update(status='timed_out' if isinstance(error, subprocess.TimeoutExpired) else 'failed', error=str(error), finished_at=time.time())
        save_json(receipt_path, receipt)
        raise
    finally:
        job.close()
