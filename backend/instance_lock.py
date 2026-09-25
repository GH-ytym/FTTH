"""An OS-owned lock: released automatically on process exit, never a PID guess."""
import os


class DataDirectoryLock:
    def __init__(self, directory):
        self.handle = open(directory / "service.lock", "a+b")
        self.handle.seek(0, 2)
        if self.handle.tell() == 0:
            self.handle.write(b"0")
            self.handle.flush()
        self.handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self.handle.close()
            raise ValueError("此数据目录已有后端运行；请关闭原后端后再启动") from exc

    def close(self):
        if not self.handle.closed:
            self.handle.close()
