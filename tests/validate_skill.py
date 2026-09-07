#!/usr/bin/env python3
"""验证公开 Skill 包的结构、引用、安全边界和只读探针。"""

from __future__ import annotations

import json
import os
import py_compile
import re
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Dict


NAME = "ship-software-end-to-end"
REPO_ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = REPO_ROOT / "skills" / NAME
PROBE = SKILL_ROOT / "scripts" / "project_delivery_probe.py"

REQUIRED_FILES = {
    "SKILL.md",
    "agents/openai.yaml",
    "references/evidence-templates.md",
    "references/project-discovery.md",
    "references/release-and-recovery.md",
    "references/verification-matrix.md",
    "scripts/project_delivery_probe.py",
}

ALLOWED_TOP_LEVEL = {"SKILL.md", "agents", "references", "scripts"}

FORBIDDEN_PUBLIC_PATTERNS = {
    "本机用户目录": re.compile(r"/(?:Users|home)/[^/\s]+/", re.IGNORECASE),
    "Windows 用户目录": re.compile(r"[A-Za-z]:\\Users\\[^\\\s]+\\", re.IGNORECASE),
    "私钥正文": re.compile(r"BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY"),
    "GitHub 令牌": re.compile(r"(?:gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,})"),
    "AWS Access Key": re.compile(r"AKIA[0-9A-Z]{16}"),
}


def fail(message: str) -> None:
    raise AssertionError(message)


def run(command: list[str], cwd: Path | None = None) -> str:
    result = subprocess.run(
        command,
        cwd=cwd,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=20,
    )
    if result.returncode != 0:
        fail(
            f"命令失败（{result.returncode}）：{' '.join(command)}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result.stdout


def parse_frontmatter(text: str) -> Dict[str, str]:
    match = re.match(r"\A---\n(?P<body>.*?)\n---\n", text, re.DOTALL)
    if not match:
        fail("SKILL.md 缺少有效 YAML frontmatter")

    values: Dict[str, str] = {}
    for raw_line in match.group("body").splitlines():
        if not raw_line.strip():
            continue
        if ":" not in raw_line:
            fail(f"无法解析 frontmatter 行：{raw_line}")
        key, value = raw_line.split(":", 1)
        key = key.strip()
        value = value.strip()
        if value.startswith('"'):
            try:
                value = json.loads(value)
            except json.JSONDecodeError as exc:
                fail(f"frontmatter 字符串无效：{exc}")
        values[key] = value
    return values


def validate_structure() -> None:
    if not SKILL_ROOT.is_dir():
        fail(f"Skill 目录不存在：{SKILL_ROOT}")

    actual_files = {
        path.relative_to(SKILL_ROOT).as_posix()
        for path in SKILL_ROOT.rglob("*")
        if path.is_file()
    }
    missing = sorted(REQUIRED_FILES - actual_files)
    if missing:
        fail(f"缺少必需文件：{', '.join(missing)}")

    unexpected_top = sorted(
        path.name for path in SKILL_ROOT.iterdir() if path.name not in ALLOWED_TOP_LEVEL
    )
    if unexpected_top:
        fail(f"Skill 包顶层存在非必要内容：{', '.join(unexpected_top)}")

    if any("__pycache__" in path.parts or path.suffix == ".pyc" for path in SKILL_ROOT.rglob("*")):
        fail("Skill 包包含 Python 运行缓存")

    skill_text = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
    metadata = parse_frontmatter(skill_text)
    if set(metadata) != {"name", "description"}:
        fail("SKILL.md frontmatter 只能包含 name 和 description")
    if metadata["name"] != NAME:
        fail(f"Skill name 应为 {NAME}")
    if not metadata["description"].strip():
        fail("Skill description 不得为空")
    if len(skill_text.splitlines()) >= 500:
        fail("SKILL.md 应保持在 500 行以内")

    openai_yaml = (SKILL_ROOT / "agents" / "openai.yaml").read_text(encoding="utf-8")
    for key in ("display_name", "short_description", "default_prompt"):
        if not re.search(rf"^\s{{2}}{key}:\s+.+$", openai_yaml, re.MULTILINE):
            fail(f"agents/openai.yaml 缺少 {key}")
    if f"${NAME}" not in openai_yaml:
        fail("default_prompt 必须显式引用 Skill 名称")


def validate_links_and_public_content() -> None:
    skill_texts: list[str] = []
    for path in sorted(SKILL_ROOT.rglob("*")):
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            fail(f"文件不是 UTF-8 文本：{path.relative_to(SKILL_ROOT)}")
        skill_texts.append(text)
        if path.suffix == ".md":
            for target in re.findall(r"\[[^\]]*\]\(([^)]+)\)", text):
                target = target.strip().split("#", 1)[0]
                if not target or target.startswith(("http://", "https://", "mailto:")):
                    continue
                resolved = (path.parent / target).resolve()
                try:
                    resolved.relative_to(SKILL_ROOT.resolve())
                except ValueError:
                    fail(f"Markdown 引用越出 Skill 目录：{path.name} -> {target}")
                if not resolved.exists():
                    fail(f"Markdown 引用不存在：{path.name} -> {target}")

    repository_texts: list[str] = []
    for path in sorted(REPO_ROOT.rglob("*")):
        if not path.is_file() or ".git" in path.parts:
            continue
        try:
            repository_texts.append(path.read_text(encoding="utf-8"))
        except UnicodeDecodeError:
            continue

    combined = "\n".join(repository_texts)
    for label, pattern in FORBIDDEN_PUBLIC_PATTERNS.items():
        if pattern.search(combined):
            fail(f"公开内容扫描命中：{label}")
    if re.search(r"\b(?:TODO|FIXME)\b", "\n".join(skill_texts)):
        fail("Skill 包仍包含 TODO/FIXME")


def validate_probe() -> None:
    mode = PROBE.stat().st_mode
    if not mode & stat.S_IXUSR:
        fail("项目探针缺少可执行权限")
    with tempfile.TemporaryDirectory(prefix="ship-software-compile-") as compile_dir:
        py_compile.compile(
            str(PROBE),
            cfile=str(Path(compile_dir) / "project_delivery_probe.pyc"),
            doraise=True,
        )

    version = run([sys.executable, str(PROBE), "--version"]).strip()
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        fail(f"项目探针版本格式无效：{version}")
    if os.environ.get("GITHUB_REF_TYPE") == "tag":
        expected_tag = f"v{version}"
        if os.environ.get("GITHUB_REF_NAME") != expected_tag:
            fail(f"发布标签必须与探针版本一致：预期 {expected_tag}")

    with tempfile.TemporaryDirectory(prefix="ship-software-probe-") as temp_dir:
        temp_root = Path(temp_dir)
        fixture = temp_root / "demo-project"
        (fixture / ".github" / "workflows").mkdir(parents=True)
        (fixture / "src").mkdir()
        (fixture / "migrations").mkdir()
        (fixture / "tests").mkdir()
        (fixture / "broken").mkdir()

        (fixture / "README.md").write_text("# Demo\n", encoding="utf-8")
        (fixture / "AGENTS.md").write_text("只读测试夹具。\n", encoding="utf-8")
        (fixture / ".env").write_text("PRIVATE_TOKEN=fixture-never-leak\n", encoding="utf-8")
        (fixture / "package.json").write_text(
            json.dumps(
                {
                    "name": "delivery-probe-fixture",
                    "scripts": {
                        "build": "tsc",
                        "test": "node --test",
                        "deploy": "API_KEY=script-secret deploy-tool",
                    },
                    "engines": {"node": ">=20"},
                }
            ),
            encoding="utf-8",
        )
        (fixture / "broken" / "package.json").write_text("{invalid\n", encoding="utf-8")
        (fixture / "tsconfig.json").write_text("{}\n", encoding="utf-8")
        (fixture / "src" / "index.ts").write_text("export const ok = true;\n", encoding="utf-8")
        (fixture / "tests" / "test_health.py").write_text("def test_ok(): assert True\n", encoding="utf-8")
        (fixture / "migrations" / "001_init.sql").write_text("SELECT 1;\n", encoding="utf-8")
        (fixture / ".github" / "workflows" / "ci.yml").write_text(
            "name: CI\non: [push]\n", encoding="utf-8"
        )

        run(["git", "init", "--quiet", "--initial-branch=main"], cwd=fixture)
        run(["git", "config", "user.name", "Probe Fixture"], cwd=fixture)
        run(["git", "config", "user.email", "probe@example.invalid"], cwd=fixture)
        run(["git", "add", "."], cwd=fixture)
        run(["git", "commit", "--quiet", "-m", "initial"], cwd=fixture)

        remote = temp_root / "remote.git"
        run(["git", "init", "--bare", "--quiet", "--initial-branch=main", str(remote)])
        run(["git", "remote", "add", "origin", str(remote)], cwd=fixture)
        run(["git", "push", "--quiet", "--set-upstream", "origin", "main"], cwd=fixture)

        peer = temp_root / "peer"
        run(["git", "clone", "--quiet", str(remote), str(peer)])
        run(["git", "config", "user.name", "Probe Peer"], cwd=peer)
        run(["git", "config", "user.email", "peer@example.invalid"], cwd=peer)
        (peer / "peer-change.txt").write_text("peer\n", encoding="utf-8")
        run(["git", "add", "peer-change.txt"], cwd=peer)
        run(["git", "commit", "--quiet", "-m", "peer change"], cwd=peer)
        run(["git", "push", "--quiet"], cwd=peer)

        run(["git", "fetch", "--quiet", "origin"], cwd=fixture)
        (fixture / "local-change.txt").write_text("local\n", encoding="utf-8")
        run(["git", "add", "local-change.txt"], cwd=fixture)
        run(["git", "commit", "--quiet", "-m", "local change"], cwd=fixture)

        outside_package = temp_root / "outside-package.json"
        outside_package.write_text(
            '{"scripts":{"test":"OUTSIDE_SECRET=symlink-never-leak"}}\n',
            encoding="utf-8",
        )
        (fixture / "linked").mkdir()
        (fixture / "linked" / "package.json").symlink_to(outside_package)
        (fixture / "dirty.txt").write_text("dirty\n", encoding="utf-8")

        status_before = run(["git", "status", "--porcelain=v1"], cwd=fixture)

        json_output = run(
            [
                sys.executable,
                str(PROBE),
                "--root",
                str(fixture),
                "--format",
                "json",
            ]
        )
        status_after = run(["git", "status", "--porcelain=v1"], cwd=fixture)
        if status_after != status_before:
            fail("探针改变了目标项目的 Git 状态")

        report = json.loads(json_output)
        if report["probe_version"] != version:
            fail("JSON 报告版本与 --version 不一致")
        if "JavaScript/Node.js" not in report["stacks"] or "TypeScript" not in report["stacks"]:
            fail("探针未识别 JavaScript/TypeScript 测试项目")
        if not report["ci_files"] or not report["migration_files"] or not report["test_files"]:
            fail("探针未识别 CI、迁移或测试线索")
        if report["git"].get("ahead") != 1 or report["git"].get("behind") != 1:
            fail("探针未识别 Git ahead/behind 状态")
        if report["git"].get("dirty_count", 0) < 1:
            fail("探针未识别脏工作树")
        if not any(package.get("error") for package in report["package_scripts"]):
            fail("探针未安全报告损坏的 package.json")
        if any(
            "command" in script
            for package in report["package_scripts"]
            for script in package.get("scripts", [])
        ):
            fail("探针不应输出 package.json 脚本正文")
        if any(
            package.get("path") == "linked/package.json"
            for package in report["package_scripts"]
        ):
            fail("探针读取了指向扫描根目录外的文件符号链接")
        if any(
            secret in json_output
            for secret in ("fixture-never-leak", "script-secret", "symlink-never-leak")
        ):
            fail("探针输出泄露了秘密正文")

        truncated_output = run(
            [
                sys.executable,
                str(PROBE),
                "--root",
                str(fixture),
                "--format",
                "json",
                "--max-files",
                "1",
            ]
        )
        if not json.loads(truncated_output)["scan"]["truncated"]:
            fail("探针未报告文件扫描截断")

        markdown_output = run(
            [sys.executable, str(PROBE), "--root", str(fixture), "--format", "markdown"]
        )
        if not markdown_output.startswith("# 项目交付只读探测"):
            fail("探针 Markdown 输出缺少预期标题")
        if any(
            secret in markdown_output
            for secret in ("fixture-never-leak", "script-secret", "symlink-never-leak")
        ):
            fail("探针 Markdown 输出泄露了秘密正文")

        plain = temp_root / "plain-project"
        plain.mkdir()
        (plain / "README.md").write_text("# Plain\n", encoding="utf-8")
        plain_report = json.loads(
            run([sys.executable, str(PROBE), "--root", str(plain), "--format", "json"])
        )
        if plain_report["git"].get("is_repository"):
            fail("探针把非 Git 目录误报为 Git 仓库")


def main() -> int:
    validate_structure()
    validate_links_and_public_content()
    validate_probe()
    print("验证通过：Skill 结构、公开内容和只读探针均符合预期。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
