"""本工具自己的状态文件；原子替换与进程间锁，不接触浏览器私人数据。"""
import json
import os
from pathlib import Path
import threading
import time


class JsonStore:
    def __init__(self, path):
        self.path = Path(path)
        self.lock = threading.RLock()

    def read(self):
        try:
            result = json.loads(self.path.read_text(encoding='utf-8'))
            if not isinstance(result, dict):
                raise ValueError('状态格式无效')
            return result
        except FileNotFoundError:
            return {}

    def transact(self, update):
        with self.lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with Path(str(self.path) + '.lock').open('a+b') as stream:
                if stream.tell() == 0:
                    stream.write(b'0')
                    stream.flush()
                if os.name == 'nt':
                    import msvcrt
                    deadline = time.monotonic() + 2
                    while True:
                        try:
                            stream.seek(0)
                            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                            break
                        except OSError:
                            if time.monotonic() >= deadline:
                                raise OSError('状态文件正在被其他工具实例使用')
                            time.sleep(0.02)
                try:
                    data = self.read()
                    result = update(data)
                    temp = Path(str(self.path) + '.tmp')
                    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
                    temp.replace(self.path)
                    return result
                finally:
                    if os.name == 'nt':
                        stream.seek(0)
                        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
