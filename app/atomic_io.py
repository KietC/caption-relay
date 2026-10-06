"""Bounded retries for Windows readers briefly holding an atomic-write target.

对 Windows 读进程短暂占用的目标文件进行有界重试，仍保持原子替换。
"""
import os
import time


_RETRY_DELAYS = (0.005, 0.010, 0.020, 0.040, 0.080, 0.120)
_TRANSIENT_WINERRORS = frozenset((5, 32, 33))


def replace_with_retry(source, destination):
    """Atomically replace, waiting at most 275 ms for transient Windows locks.

    The successful fast path does not sleep. Permanent errors remain visible;
    there is no delete/copy fallback that could expose a partially written file.

    对短暂的 Windows 文件锁最多等待 275 毫秒后完成原子替换。
    成功的快速路径不等待；永久错误继续向调用方抛出。
    不使用删除后复制作为降级方案，避免读者看到写了一半的文件。
    """
    for attempt in range(len(_RETRY_DELAYS) + 1):
        try:
            os.replace(source, destination)
            return
        except PermissionError as error:
            if (getattr(error, "winerror", None) not in _TRANSIENT_WINERRORS
                    or attempt == len(_RETRY_DELAYS)):
                raise
            time.sleep(_RETRY_DELAYS[attempt])
