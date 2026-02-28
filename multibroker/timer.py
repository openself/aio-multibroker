import logging
import time

LOG = logging.getLogger(__name__)


def get_current_time_ms():
    return time.time_ns() // 1000000


class Timer:
    def __init__(self, name: str, active: bool = True) -> None:
        self.name = name
        self.active = active

        self.start_tmstmp_ms = None

    def __enter__(self) -> None:
        if self.active:
            self.start_tmstmp_ms = get_current_time_ms()

    def __exit__(self, type, value, traceback) -> None:
        if self.active:
            LOG.debug(
                f'Timer {self.name} finished. Took {round((get_current_time_ms() - self.start_tmstmp_ms), 3)} ms.'
            )
