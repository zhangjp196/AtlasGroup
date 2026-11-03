"""自愈日志文件处理器：logs 目录/日志文件被删除后自动重建，避免运行期日志静默丢失。"""
from __future__ import annotations

import os
import time
from logging.handlers import TimedRotatingFileHandler


class SelfHealingTimedRotatingFileHandler(TimedRotatingFileHandler):
    """按天轮转的文件 handler，父目录或日志文件被外部删除时：

    - 打开文件前自动重建父目录；
    - 轮转时若原文件已不存在则跳过 rename 直接重建文件，
      避免标准 doRollover 在目录缺失时持续抛 FileNotFoundError。
    """

    def _open(self):
        base_dir = os.path.dirname(self.baseFilename)
        if base_dir:
            os.makedirs(base_dir, exist_ok=True)
        return super()._open()

    def doRollover(self):
        base_dir = os.path.dirname(self.baseFilename)
        if base_dir:
            os.makedirs(base_dir, exist_ok=True)
        if not os.path.exists(self.baseFilename):
            if self.stream:
                self.stream.close()
                self.stream = None
            self.stream = self._open()
            self.rolloverAt = self.computeRollover(int(time.time()))
            return
        super().doRollover()
