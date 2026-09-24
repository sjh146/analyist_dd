"""임베딩 디바이스 해석 유틸 (torch 비의존 순수 함수).

GPU 가 없는 현재 환경(CPU 경로)에서도, 나중에 GPU 가 생겼을 때 코드 수정
없이 ``EMBEDDING_DEVICE`` / ``EMBEDDING_BATCH_SIZE`` env 만으로 전환할 수
있도록 디바이스 선택 로직을 순수 함수로 분리한다.

이 모듈은 torch / sentence-transformers 를 import 하지 않으므로, 무거운
의존성 없이도 단위 테스트가 가능하다.
"""

from dataclasses import dataclass
from typing import Optional
import warnings


@dataclass(frozen=True)
class DeviceResolution:
    """디바이스 해석 결과.

    - ``device``: 실제로 사용할 device 이름 ("cpu" / "cuda" / "mps").
    - ``warning``: 요청한 device 를 쓸 수 없어 폴백했거나 알 수 없는 값일 때
      그 사유 문자열. 정상 선택이면 ``None``.
    """

    device: str
    warning: Optional[str] = None


def resolve_embedding_device(
    requested: str,
    cuda_available: bool,
    mps_available: bool = False,
) -> DeviceResolution:
    """``EMBEDDING_DEVICE`` 값을 실제 device 문자열로 해석한다.

    - ``auto``: cuda 사용 가능하면 ``cuda``, 아니면 mps, 그것도 아니면 ``cpu``.
    - ``cuda`` / ``mps``: 명시 요청. 사용 불가면 ``cpu`` 로 폴백하되 경고를
      남긴다(운영은 멈추지 않는다).
    - ``cpu``: 그대로 ``cpu``.
    - 그 외(알 수 없는 값): ``cpu`` + 경고.
    """
    req = (requested or "auto").strip().lower()

    if req == "auto":
        if cuda_available:
            return DeviceResolution("cuda")
        if mps_available:
            return DeviceResolution("mps")
        return DeviceResolution("cpu")

    if req == "cuda":
        if cuda_available:
            return DeviceResolution("cuda")
        return DeviceResolution(
            "cpu",
            "cuda requested but not available; falling back to cpu",
        )

    if req == "mps":
        if mps_available:
            return DeviceResolution("mps")
        return DeviceResolution(
            "cpu",
            "mps requested but not available; falling back to cpu",
        )

    if req == "cpu":
        return DeviceResolution("cpu")

    return DeviceResolution(
        "cpu",
        f"unknown device '{requested}'; falling back to cpu",
    )


def resolve_batch_size(raw: Optional[str], default: int = 32) -> int:
    """``EMBEDDING_BATCH_SIZE`` 값을 양의 정수로 파싱한다.

    비었거나 잘못된 값이면 ``default``(32)로 떨어진다.
    """
    if raw is None:
        return default
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return default
    if value <= 0:
        return default
    return value


def resolve_torch_threads(requested: Optional[str]) -> Optional[int]:
    """``EMBEDDING_TORCH_THREADS`` 값을 양의 정수 스레드 수로 파싱한다.

    - ``None`` 또는 공백 문자열: ``None`` (torch 기본 동작 유지).
    - 양의 정수: 해당 값.
    - 그 외(0, 음수, 비정수): ``None`` + ``UserWarning`` 경고 신호.

    torch 를 import 하지 않으므로 단위 테스트가 가볍다.
    """
    if requested is None:
        return None
    text = str(requested).strip()
    if text == "":
        return None
    try:
        value = int(text)
    except (TypeError, ValueError):
        warnings.warn(
            f"EMBEDDING_TORCH_THREADS='{requested}' is not a valid integer; "
            "keeping torch default thread count",
            stacklevel=2,
        )
        return None
    if value <= 0:
        warnings.warn(
            f"EMBEDDING_TORCH_THREADS='{requested}' must be a positive integer; "
            "keeping torch default thread count",
            stacklevel=2,
        )
        return None
    return value


def detect_torch_device_flags():
    """(cuda_available, mps_available) 을 반환한다.

    torch 가 설치돼 있지 않으면 ``(False, False)``. torch import 는 함수
    내부에서만 수행하므로 이 모듈 자체는 torch 없이 import 가능하다.
    """
    try:
        import torch
    except Exception:
        return False, False

    cuda = False
    mps = False
    try:
        cuda = bool(torch.cuda.is_available())
    except Exception:
        cuda = False

    mps_backend = getattr(torch.backends, "mps", None)
    if mps_backend is not None:
        try:
            mps = bool(mps_backend.is_available())
        except Exception:
            mps = False

    return cuda, mps
