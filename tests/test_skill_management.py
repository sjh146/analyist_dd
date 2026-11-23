"""스킬 관리(미러·린트) 회귀 — 2026-11-23 도입.

미러: ~/.hermes/skills/{quant,trading} → 리포 config/skills, 변경 시 커밋.
린트: frontmatter·트리거 문구·references 링크·크기 경고.
"""
from __future__ import annotations

import importlib.util
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))


def _load(mod_name: str, rel: str):
    spec = importlib.util.spec_from_file_location(mod_name, REPO / rel)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def _skill(root: pathlib.Path, group: str, name: str, body: str) -> pathlib.Path:
    d = root / group / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(body, encoding="utf-8")
    return d


GOOD = """---
name: demo-skill
description: Use when testing the linter. Adds a demo body.
---

# demo
"""


def test_frontmatter_parsed() -> None:
    mod = _load("lint_skills_under_test", "scripts/lint_skills.py")
    meta = mod.parse_frontmatter(GOOD)
    assert meta["name"] == "demo-skill"
    assert meta["description"].startswith("Use when")


def test_clean_skill_has_no_errors(tmp_path) -> None:
    mod = _load("lint_skills_under_test", "scripts/lint_skills.py")
    d = _skill(tmp_path, "trading", "demo-skill", GOOD)
    out = mod.lint_skill(str(d))
    assert out["errors"] == []


def test_missing_frontmatter_is_error(tmp_path) -> None:
    mod = _load("lint_skills_under_test", "scripts/lint_skills.py")
    d = _skill(tmp_path, "trading", "bad", "# no frontmatter\n")
    assert any("E001" in e for e in mod.lint_skill(str(d))["errors"])


def test_name_mismatch_and_trigger_are_errors(tmp_path) -> None:
    mod = _load("lint_skills_under_test", "scripts/lint_skills.py")
    d = _skill(tmp_path, "trading", "dir-name",
               "---\nname: other\ndescription: audit things\n---\n")
    errors = mod.lint_skill(str(d))["errors"]
    assert any("E002" in e for e in errors)
    assert any("E004" in e for e in errors)


def test_broken_reference_link_is_error(tmp_path) -> None:
    mod = _load("lint_skills_under_test", "scripts/lint_skills.py")
    body = GOOD + "\n자세히: `references/missing.md` 보기\n"
    d = _skill(tmp_path, "quant", "demo-skill", body)
    assert any("E005" in e for e in mod.lint_skill(str(d))["errors"])


def test_reference_link_present_is_fine(tmp_path) -> None:
    mod = _load("lint_skills_under_test", "scripts/lint_skills.py")
    body = GOOD + "\n자세히: `references/ok.md` 보기\n"
    d = _skill(tmp_path, "quant", "demo-skill", body)
    (d / "references").mkdir()
    (d / "references" / "ok.md").write_text("x", encoding="utf-8")
    assert mod.lint_skill(str(d))["errors"] == []


def test_size_warning(tmp_path) -> None:
    mod = _load("lint_skills_under_test", "scripts/lint_skills.py")
    big = GOOD + ("x" * (mod.MAX_KB * 1024))
    d = _skill(tmp_path, "trading", "demo-skill", big)
    assert any("W001" in w for w in mod.lint_skill(str(d))["warnings"])


def test_diff_tree_detects_add_change_delete(tmp_path) -> None:
    mod = _load("sync_skills_under_test", "scripts/sync_skills.py")
    src, dst = tmp_path / "src", tmp_path / "dst"
    (src / "a").mkdir(parents=True)
    (src / "a" / "f1.md").write_text("new", encoding="utf-8")
    (dst / "a").mkdir(parents=True)
    (dst / "a" / "f1.md").write_text("old", encoding="utf-8")
    (dst / "a" / "gone.md").write_text("x", encoding="utf-8")
    changed = mod.diff_tree(str(src), str(dst))
    assert "a/f1.md" in [c.replace("\\", "/") for c in changed]
    assert "a/gone.md" in [c.replace("\\", "/") for c in changed]


def test_sync_tree_mirrors_content_and_removals(tmp_path) -> None:
    mod = _load("sync_skills_under_test", "scripts/sync_skills.py")
    src, dst = tmp_path / "src", tmp_path / "dst"
    (src / "g" / "s").mkdir(parents=True)
    (src / "g" / "s" / "SKILL.md").write_text(GOOD, encoding="utf-8")
    (dst / "g" / "stale").mkdir(parents=True)
    (dst / "g" / "stale" / "SKILL.md").write_text("old", encoding="utf-8")
    mod.sync_tree(str(src), str(dst))
    assert (dst / "g" / "s" / "SKILL.md").read_text(encoding="utf-8") == GOOD
    assert not (dst / "g" / "stale").exists()


def test_dry_run_changes_nothing(tmp_path) -> None:
    mod = _load("sync_skills_under_test", "scripts/sync_skills.py")
    src, dst = tmp_path / "src", tmp_path / "dst"
    (src / "g").mkdir(parents=True)
    (src / "g" / "x.md").write_text("x", encoding="utf-8")
    changed = mod.sync_tree(str(src), str(dst), dry_run=True)
    assert changed and not dst.exists()
