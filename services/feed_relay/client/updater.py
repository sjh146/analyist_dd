#!/usr/bin/env python3
"""고객 PC: 서명된 manifest 로 코드 번들을 안전하게 교체한다.

순서: manifest → 서명 검증(HMAC) → zip 다운로드 → bundle sha256 → staging 해제 →
파일별 sha256 검증 → 현재 app 백업 → 교체 → 실패 시 롤백.
장중 실행 금지(호출자가 시간을 통제한다).
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import shutil
import sys
import tempfile
import time
import urllib.request
import zipfile


def canonical(payload: dict) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def verify_manifest(manifest: dict, release_key: str) -> bool:
    sig = str(manifest.get("signature") or "")
    body = {k: v for k, v in manifest.items() if k not in ("signature", "customer_id")}
    want = hmac.new(release_key.encode("utf-8"), canonical(body), hashlib.sha256).hexdigest()
    return hmac.compare_digest(sig, want)


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def fetch_manifest(base_url: str, channel: str, customer_id: str, token: str,
                   timeout: float = 20.0) -> dict:
    url = "{0}/v1/manifest?channel={1}&customer_id={2}".format(
        base_url.rstrip("/"), channel, customer_id)
    req = urllib.request.Request(url, headers={"Authorization": "Bearer " + token})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def download(base_url: str, version: str, dest: str, timeout: float = 120.0) -> str:
    url = "{0}/v1/pkg/{1}".format(base_url.rstrip("/"), version)
    with urllib.request.urlopen(url, timeout=timeout) as resp, open(dest, "wb") as fh:
        shutil.copyfileobj(resp, fh)
    return dest


def extract_verified(bundle: str, staging: str, files: list[dict]) -> None:
    if os.path.isdir(staging):
        shutil.rmtree(staging)
    os.makedirs(staging)
    with zipfile.ZipFile(bundle) as zf:
        zf.extractall(staging)
    for item in files:
        path = os.path.join(staging, item["path"])
        if not os.path.isfile(path):
            raise ValueError("누락 파일: {0}".format(item["path"]))
        if sha256_file(path) != item["sha256"]:
            raise ValueError("sha 불일치: {0}".format(item["path"]))


def current_version(app_dir: str) -> str:
    try:
        with open(os.path.join(app_dir, "version.json"), encoding="utf-8") as fh:
            return str(json.load(fh).get("version") or "")
    except (OSError, ValueError):
        return ""


def apply_release(app_dir: str, staging: str, version: str) -> None:
    parent = os.path.dirname(os.path.abspath(app_dir))
    os.makedirs(parent, exist_ok=True)
    backup = app_dir.rstrip("/\\") + ".bak-" + time.strftime("%Y%m%d%H%M%S")
    had_app = os.path.isdir(app_dir)
    if had_app:
        os.replace(app_dir, backup)
    try:
        os.replace(staging, app_dir)
        with open(os.path.join(app_dir, "version.json"), "w", encoding="utf-8") as fh:
            json.dump({"version": version, "applied_at": time.strftime("%Y-%m-%dT%H:%M:%S")}, fh)
    except OSError:
        if os.path.isdir(app_dir):
            shutil.rmtree(app_dir, ignore_errors=True)
        if had_app and os.path.isdir(backup):
            os.replace(backup, app_dir)
        raise
    if had_app:
        shutil.rmtree(backup, ignore_errors=True)


def update(cfg: dict, force: bool = False) -> dict:
    app_dir = cfg["app_dir"]
    manifest = fetch_manifest(cfg["base_url"], cfg.get("channel") or "stable",
                              cfg.get("customer_id") or "", cfg.get("token") or "")
    if not verify_manifest(manifest, cfg["release_key"]):
        raise SystemExit("manifest 서명 검증 실패 — 업데이트 중단")
    version = str(manifest.get("version") or "")
    have = current_version(app_dir)
    if have == version and not force:
        return {"ok": True, "changed": False, "version": version}
    min_client = str(manifest.get("min_client_version") or "")
    if min_client and have and have < min_client:
        pass
    parent = os.path.dirname(os.path.abspath(app_dir))
    os.makedirs(parent, exist_ok=True)
    work = tempfile.mkdtemp(prefix=".relay-update-", dir=parent)
    try:
        bundle = download(cfg["base_url"], version, os.path.join(work, "bundle.zip"))
        if sha256_file(bundle) != manifest.get("bundle_sha256"):
            raise SystemExit("bundle sha 불일치 — 업데이트 중단")
        staging = os.path.join(work, "app")
        extract_verified(bundle, staging, manifest.get("files") or [])
        apply_release(app_dir, staging, version)
        return {"ok": True, "changed": True, "version": version,
                "files": len(manifest.get("files") or [])}
    finally:
        shutil.rmtree(work, ignore_errors=True)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="코드 번들 업데이트")
    ap.add_argument("--config", default="config.json")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args(argv)
    with open(args.config, encoding="utf-8") as fh:
        cfg = json.load(fh)
    print(json.dumps(update(cfg, force=args.force), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception as exc:
        print("update 실패: {0}".format(exc), file=sys.stderr)
        raise SystemExit(1)
