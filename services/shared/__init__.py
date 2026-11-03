# 平台级 shared（runtime、process、arq 等）；与各服务 shared/ 通过 extend_path 合并。
from pkgutil import extend_path

__path__ = extend_path(__path__, __name__)
