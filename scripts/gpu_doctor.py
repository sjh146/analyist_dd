#!/usr/bin/env python3
"""GPU 진단 도구 (scripts/gpu_doctor.py).

호스트의 GPU/WSL 상태와 각 컨테이너의 torch CUDA 가용성, 그리고 각 서비스가
실제로 선택할 ``EMBEDDING_DEVICE`` 해석 결과를 출력한다.

- GPU 가 없으면 "컨테이너 없음/GPU 없음" 으로 보고하고 정상 종료(exit 0).
- ``--require-gpu`` 를 주면 GPU 가 없을 때 비영(非0) 종료 → GPU 세팅 검증용.

사용법:
  python3 scripts/gpu_doctor.py
  python3 scripts/gpu_doctor.py --require-gpu
"""

import argparse
import importlib.util
import os
import shutil
import subprocess
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEVICE_MODULE_PATH = os.path.join(
    PROJECT_ROOT,
    "services",
    "news-analyzer",
    "app",
    "embedding",
    "device.py",
)

# (docker compose 서비스명, 컨테이너명, EMBEDDING_DEVICE 해석 대상 여부)
SERVICES = [
    ("news-analyzer", "stock_news_analyzer", True),
    ("stock-vectorizer", "stock_vectorizer", True),
    ("xgboost-ml", "stock_xgboost_ml", False),
]


def run(cmd):
    """Run a command, return (returncode, stdout, stderr). Never raises."""
    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=60,
        )
        return proc.returncode, proc.stdout.strip(), proc.stderr.strip()
    except FileNotFoundError:
        return -1, "", "command not found"
    except Exception as e:  # noqa: BLE001 - 진단 도구: 어떤 실패도 삼킨다
        return -1, "", str(e)


def load_device_module():
    """device.py 를 파일 경로에서 로드 (torch/dotenv 없이)."""
    try:
        spec = importlib.util.spec_from_file_location("_gpu_doctor_device", DEVICE_MODULE_PATH)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    except Exception as e:  # noqa: BLE001
        print(f"[경고] device.py 로드 실패: {e}")
        return None


def read_env_value(key):
    """프로젝트 .env / .env.example 에서 키 값을 읽는다 (없으면 None)."""
    for name in (".env", ".env.example"):
        path = os.path.join(PROJECT_ROOT, name)
        if not os.path.isfile(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, _, v = line.partition("=")
                    if k.strip() == key:
                        return v.strip().strip('"').strip("'")
        except Exception:  # noqa: BLE001
            continue
    return None


def host_section():
    """호스트 GPU/WSL 상태."""
    print("=== [1] 호스트 GPU/WSL 상태 ===")
    dxg = os.path.exists("/dev/dxg")
    print(f"  /dev/dxg 존재: {dxg}")

    nvidia_smi = shutil.which("nvidia-smi")
    nvcc = shutil.which("nvcc")
    print(f"  nvidia-smi 존재: {bool(nvidia_smi)} ({nvidia_smi or '없음'})")
    print(f"  nvcc 존재: {bool(nvcc)} ({nvcc or '없음'})")

    gpu_name = None
    driver_version = None
    if nvidia_smi:
        rc, out, err = run([nvidia_smi, "--query-gpu=name,driver_version", "--format=csv,noheader"])
        if rc == 0 and out:
            first = out.splitlines()[0]
            parts = [p.strip() for p in first.split(",")]
            if len(parts) >= 2:
                gpu_name, driver_version = parts[0], parts[1]
            print(f"  nvidia-smi GPU 목록: {first}")
        else:
            print(f"  nvidia-smi 실행 실패 (rc={rc}): {err or out}")
    else:
        print("  nvidia-smi 없음 → CUDA 사용 불가")

    has_gpu = bool(nvidia_smi) and gpu_name is not None
    print(f"  호스트 GPU 사용 가능: {has_gpu}")
    return has_gpu


def container_section(device_mod):
    """각 컨테이너의 torch CUDA 상태 + EMBEDDING_DEVICE 해석."""
    print("\n=== [2] 컨테이너별 torch CUDA 상태 ===")
    env_device = read_env_value("EMBEDDING_DEVICE") or "auto"
    env_batch = read_env_value("EMBEDDING_BATCH_SIZE")
    print(f"  .env EMBEDDING_DEVICE: {env_device} / EMBEDDING_BATCH_SIZE: {env_batch or '(미설정=32)'}")

    docker = shutil.which("docker")
    if not docker:
        print("  docker 명령 없음 → 컨테이너 검사 생략")
        return

    for service, container, resolves_device in SERVICES:
        rc, state, _ = run([docker, "inspect", "-f", "{{.State.Running}}", container])
        running = rc == 0 and state.strip() == "true"

        if not running:
            print(f"  [{service}] 컨테이너 없음/미실행 ({container})")
            if resolves_device and device_mod:
                # 컨테이너가 없으면 호스트 추정치로 해석 (auto → cpu)
                res = device_mod.resolve_embedding_device(env_device, False, False)
                warn = f" (경고: {res.warning})" if res.warning else ""
                print(f"      → EMBEDDING_DEVICE '{env_device}' 해석: '{res.device}'{warn} [컨테이너 없음: cuda=False 가정]")
            continue

        # torch.cuda.is_available + 디바이스명
        py = (
            "import torch; "
            "print(torch.cuda.is_available()); "
            "print(torch.cuda.get_device_name(0) if torch.cuda.is_available() else '')"
        )
        rc, out, err = run([docker, "exec", container, "python", "-c", py])
        if rc != 0:
            print(f"  [{service}] 실행 중이나 torch 검사 실패: {err or out}")
            cuda = False
            device_name = ""
        else:
            lines = out.splitlines()
            cuda = lines[0].strip() == "True" if lines else False
            device_name = lines[1].strip() if len(lines) > 1 else ""
            print(
                f"  [{service}] torch.cuda.is_available()={cuda}"
                + (f" / device={device_name}" if device_name else "")
            )

        if resolves_device and device_mod:
            res = device_mod.resolve_embedding_device(env_device, cuda, False)
            warn = f" (경고: {res.warning})" if res.warning else ""
            print(f"      → EMBEDDING_DEVICE '{env_device}' 해석: '{res.device}'{warn}")


def main():
    parser = argparse.ArgumentParser(description="GPU 진단 도구")
    parser.add_argument(
        "--require-gpu",
        action="store_true",
        help="GPU 가 없으면 비영(非0) 종료",
    )
    args = parser.parse_args()

    device_mod = load_device_module()
    has_gpu = host_section()
    container_section(device_mod)

    print("\n=== [3] 결과 ===")
    if has_gpu:
        print("  GPU 사용 가능. GPU 오버레이 + EMBEDDING_DEVICE=auto 로 전환 가능.")
        sys.exit(0)
    print("  GPU 없음. 현재 CPU 경로 유지 (기본 동작).")
    if args.require_gpu:
        print("  --require-gpu 지정됨 → 비영 종료 (GPU 세팅 검증 실패)")
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
