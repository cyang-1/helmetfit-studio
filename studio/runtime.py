import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ACTIVE = {'queued', 'generating', 'preparing', 'repairing', 'fitting', 'slicing'}


class Cancelled(Exception):
    pass


def blender_path():
    candidates = [os.getenv('BLENDER_EXE'), ROOT / 'blender/blender.exe',
                  ROOT.parent / 'HelmetFit-Windows-Portable-v1.0/blender/blender.exe',
                  Path(os.getenv('ProgramFiles', 'C:/Program Files')) / 'Blender Foundation/Blender 4.5/blender.exe']
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return Path(candidate)
    raise ValueError('找不到 Blender 4.5，请安装后重启，或设置 BLENDER_EXE。')


def native_environment(private_home=None):
    """Give mesh parsers their system libraries and local settings, not server credentials."""
    names = {
        'PATH', 'SYSTEMROOT', 'WINDIR', 'SYSTEMDRIVE', 'COMSPEC', 'PATHEXT', 'PROGRAMDATA',
        'PROGRAMFILES', 'PROGRAMFILES(X86)', 'COMMONPROGRAMFILES', 'COMMONPROGRAMFILES(X86)',
        'USERPROFILE', 'APPDATA', 'LOCALAPPDATA', 'HOME', 'TMPDIR', 'TMP', 'TEMP', 'LANG',
        'LC_ALL', 'LC_CTYPE', 'DISPLAY', 'XAUTHORITY', 'LIBGL_ALWAYS_SOFTWARE',
        'GALLIUM_DRIVER', 'SSL_CERT_FILE', 'CUDA_VISIBLE_DEVICES',
    }
    env = {k: v for k, v in os.environ.items() if k.upper() in names}
    if private_home is not None:
        env.update(HOME=private_home, XDG_CONFIG_HOME=private_home + '/config',
                   XDG_CACHE_HOME=private_home + '/cache', XDG_DATA_HOME=private_home + '/data')
    return env


def run_process(args, folder, cancelled, timeout):
    started = time.monotonic()
    if cancelled():
        raise Cancelled()
    linux = sys.platform.startswith('linux')
    with tempfile.TemporaryDirectory(prefix='helmetfit-native-') as private, (folder / 'process.log').open('wb') as logfile:
        proc = subprocess.Popen([str(a) for a in args], cwd=folder, stdout=logfile,
                                stderr=subprocess.STDOUT, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
                                env=native_environment(private if linux else None), start_new_session=linux)
        try:
            while proc.poll() is None:
                if cancelled():
                    raise Cancelled()
                if time.monotonic() - started > timeout:
                    raise ValueError('本地处理超时。请减少模型复杂度或分件后再试。')
                time.sleep(0.25)
        finally:
            if proc.poll() is None:
                if linux:
                    try:
                        os.killpg(proc.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                else:
                    proc.kill()
                proc.wait()
    return proc.returncode


def geometry(mode, folder, source=None, params=None, cancelled=lambda: False):
    folder.mkdir(parents=True, exist_ok=True)
    report = folder / 'report.json'
    report.unlink(missing_ok=True)
    config = dict(mode=mode, directory=str(folder), params=params or {})
    if source:
        config['source'] = str(source)
    settings = folder / 'config.json'
    settings.write_text(json.dumps(config, ensure_ascii=False), encoding='utf-8')
    code = run_process([blender_path(), '-b', '--factory-startup', '--disable-autoexec', '--threads', '4', '--python-exit-code', '1',
                        '--python', ROOT / 'studio/geometry.py', '--', settings], folder, cancelled, 900)
    if not report.is_file():
        raise ValueError(f'Blender 未完成处理（退出码 {code}），请检查模型或查看任务目录的 process.log。')
    result = json.loads(report.read_text(encoding='utf-8-sig'))
    if code or result.get('error'):
        raise ValueError(result.get('error') or f'Blender 退出码 {code}')
    return result
