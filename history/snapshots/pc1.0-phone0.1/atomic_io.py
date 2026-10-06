"""Bounded retries for Windows readers briefly holding an atomic-write target."""
import os
import time


_RETRY_DELAYS = (0.005, 0.010, 0.020, 0.040, 0.080, 0.120)
_TRANSIENT_WINERRORS = frozenset((5, 32, 33))


def replace_with_retry(source, destination):
    """Atomically replace, waiting at most 275 ms for transient Windows locks.

    The successful fast path does not sleep. Permanent errors remain visible;
    there is no delete/copy fallback that could expose a partially written file.
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
