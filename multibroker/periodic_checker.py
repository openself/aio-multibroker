import datetime
import logging

LOG = logging.getLogger(__name__)


class PeriodicChecker:
    def __init__(self, period_ms):
        self.period_ms = period_ms
        self.last_exec_tmstmp_ms = int(datetime.datetime.now(tz=datetime.UTC).timestamp() * 1000)

    def check(self) -> bool:
        now_tmstmp_ms = int(datetime.datetime.now(tz=datetime.UTC).timestamp() * 1000)

        if self.last_exec_tmstmp_ms + self.period_ms < now_tmstmp_ms:
            self.last_exec_tmstmp_ms = now_tmstmp_ms

            return True

        return False
