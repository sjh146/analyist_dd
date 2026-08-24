"""SNS news-analyzer 모듈 로더.

리포는 최상위 ``app`` 패키지가 두 개(xgboost-ml/app, news-analyzer/app) 존재해
같은 pytest 프로세스에서 두 쪽을 동시에 import 하면 ``app`` 이름 충돌이 난다.
news-analyzer 모듈을 고유 네임스페이스 ``newsapp`` 아래 로드해 충돌을 회피한다.

`app.collectors.*` 는 상대 import(``from .sns_interface import SnsPost``)를
사용하므로, ``newsapp`` 와 ``newsapp.collectors`` 패키지를 sys.modules 에 미리
등록하고 각 모듈을 파일 경로로 로드한다.
"""
import importlib.util
import os
import sys
import types

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_NA_APP = os.path.join(_ROOT, "services", "news-analyzer", "app")
_loaded = {}


def _ensure_package(name, path):
    pkg = types.ModuleType(name)
    pkg.__path__ = [path]
    sys.modules[name] = pkg
    return pkg


def _load(module_name, file_path):
    if module_name in _loaded:
        return _loaded[module_name]
    mod = importlib.util.spec_from_file_location(module_name, file_path)
    module = importlib.util.module_from_spec(mod)
    sys.modules[module_name] = module
    mod.loader.exec_module(module)
    _loaded[module_name] = module
    return module


def load_sns_modules():
    """news-analyzer collectopr/triage 모듈을 newsapp 네임스페이스로 로드.

    Returns
    -------
    (sns_interface, sns_naver_board, sns_x, sns_deepseek_triage) 모듈.
    """
    _ensure_package("newsapp", _NA_APP)
    coll_dir = os.path.join(_NA_APP, "collectors")
    coll_pkg = _ensure_package("newsapp.collectors", coll_dir)
    sys.modules["newsapp"].collectors = coll_pkg

    interface = _load("newsapp.collectors.sns_interface",
                      os.path.join(coll_dir, "sns_interface.py"))
    naver = _load("newsapp.collectors.sns_naver_board",
                  os.path.join(coll_dir, "sns_naver_board.py"))
    x = _load("newsapp.collectors.sns_x",
              os.path.join(coll_dir, "sns_x.py"))
    triage = _load("newsapp.sns_deepseek_triage",
                   os.path.join(_NA_APP, "sns_deepseek_triage.py"))
    return interface, naver, x, triage
