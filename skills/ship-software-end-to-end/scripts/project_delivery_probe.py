#!/usr/bin/env python3
"""只读探测项目的交付线索，不执行项目脚本、不访问远端或生产。"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


VERSION = "1.3.0"
SKIP_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".cache",
    ".idea",
    ".terraform",
    ".venv",
    ".vscode",
    "__pycache__",
    "build",
    "coverage",
    "deriveddata",
    "dist",
    "node_modules",
    "out",
    "pods",
    "release-output",
    "target",
    "vendor",
    "venv",
}

INSTRUCTION_NAMES = {
    "agents.md",
    "claude.md",
    "codeowners",
    "contributing",
    "contributing.md",
    "readme",
    "readme.md",
    "readme.rst",
    "readme.txt",
    "security.md",
}

MANIFEST_NAMES = {
    "bun.lock",
    "cargo.toml",
    "composer.json",
    "deno.json",
    "deno.jsonc",
    "gemfile",
    "go.mod",
    "mix.exs",
    "package.json",
    "package.swift",
    "pom.xml",
    "pubspec.yaml",
    "pyproject.toml",
    "requirements.txt",
    "setup.cfg",
    "setup.py",
}

LOCK_NAMES = {
    "bun.lock",
    "bun.lockb",
    "cargo.lock",
    "composer.lock",
    "gemfile.lock",
    "go.sum",
    "package-lock.json",
    "pnpm-lock.yaml",
    "poetry.lock",
    "pubspec.lock",
    "uv.lock",
    "yarn.lock",
}

RUNTIME_PIN_NAMES = {
    ".node-version",
    ".nvmrc",
    ".python-version",
    ".ruby-version",
    ".tool-versions",
    "mise.toml",
    "rust-toolchain",
    "rust-toolchain.toml",
}

CONFIG_TEMPLATE_NAMES = {
    ".env.example",
    ".env.sample",
    "appsettings.example.json",
    "config.example.json",
    "config.example.php",
    "config.local.php.example",
}

RELEVANT_SCRIPT_RE = re.compile(
    r"(^|:|-)(build|check|ci|compile|deploy|e2e|format|lint|migrat|package|release|smoke|test|typecheck|verify)($|:|-)",
    re.IGNORECASE,
)

def run_command(command: Sequence[str], cwd: Path, timeout: int = 12) -> Tuple[bool, str]:
    """运行只读子命令并返回合并前的 stdout；不使用 shell。"""
    env = os.environ.copy()
    env.setdefault("LC_ALL", "C")
    try:
        result = subprocess.run(
            list(command),
            cwd=str(cwd),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False, ""
    return result.returncode == 0, result.stdout.strip()


def git_value(cwd: Path, args: Sequence[str]) -> Optional[str]:
    ok, output = run_command(["git", "-c", "core.quotepath=false", *args], cwd)
    return output if ok and output else None


def scan_files(root: Path, max_files: int) -> Tuple[List[str], bool]:
    paths: List[str] = []
    truncated = False
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = sorted(
            name
            for name in dirnames
            if name.lower() not in SKIP_DIRS and not Path(dirpath, name).is_symlink()
        )
        for filename in sorted(filenames):
            full_path = Path(dirpath, filename)
            if full_path.is_symlink():
                continue
            try:
                relative = full_path.relative_to(root).as_posix()
            except ValueError:
                continue
            paths.append(relative)
            if len(paths) >= max_files:
                truncated = True
                return paths, truncated
    return paths, truncated


def limited(items: Iterable[str], max_items: int) -> List[str]:
    return sorted(set(items))[:max_items]


def path_depth(path: str) -> int:
    return path.count("/")


def find_instruction_files(paths: Sequence[str], max_items: int) -> List[str]:
    matches = []
    for path in paths:
        name = Path(path).name.lower()
        if name in INSTRUCTION_NAMES or name.startswith("readme.") or name.startswith("contributing."):
            matches.append(path)
    return limited(matches, max_items)


def is_ci_file(path: str) -> bool:
    lower = path.lower()
    name = Path(lower).name
    if lower.startswith(".github/workflows/") and name.endswith((".yml", ".yaml")):
        return True
    return lower in {
        ".circleci/config.yml",
        ".circleci/config.yaml",
        ".gitlab-ci.yml",
        "azure-pipelines.yml",
        "bitbucket-pipelines.yml",
        "jenkinsfile",
    }


def is_delivery_file(path: str) -> bool:
    lower = path.lower()
    name = Path(lower).name
    parts = lower.split("/")
    fixed_names = {
        "app.yaml",
        "cloudbuild.yaml",
        "docker-compose.yml",
        "docker-compose.yaml",
        "fly.toml",
        "netlify.toml",
        "procfile",
        "serverless.yml",
        "serverless.yaml",
        "vercel.json",
        "wrangler.json",
        "wrangler.jsonc",
        "wrangler.toml",
    }
    if name in fixed_names or name.startswith("dockerfile"):
        return True
    infra_roots = {
        ".github",
        ".gitlab",
        "ansible",
        "charts",
        "deploy",
        "deployment",
        "helm",
        "infra",
        "infrastructure",
        "k8s",
        "kubernetes",
        "ops",
        "scripts",
        "terraform",
    }
    if parts and parts[0] in infra_roots:
        return any(token in lower for token in ("deploy", "release", "rollback", "smoke", "health", "backup", "restore", "migrat"))
    return name.endswith((".tf", ".tf.json"))


def is_test_file(path: str) -> bool:
    lower = path.lower()
    parts = lower.split("/")
    name = parts[-1]
    if any(part in {"test", "tests", "spec", "specs", "e2e", "__tests__"} for part in parts[:-1]):
        return True
    return bool(
        re.search(r"(^test_.*|.*[_\.-](test|spec))\.(c|cc|cpp|cs|go|java|js|jsx|mjs|php|py|rb|rs|ts|tsx)$", name)
    )


def is_migration_file(path: str) -> bool:
    lower = path.lower()
    parts = lower.split("/")
    name = parts[-1]
    if any(part in {"migration", "migrations", "alembic", "migrate"} for part in parts[:-1]):
        return True
    if "prisma/migrations/" in lower or "db/migrate/" in lower:
        return True
    return name in {"schema.sql", "schema.prisma"}


def detect_stacks(paths: Sequence[str]) -> List[str]:
    lower_paths = [path.lower() for path in paths]
    basenames = {Path(path).name.lower() for path in paths}
    stacks = []
    if any(Path(path).name.lower() == "package.json" for path in paths):
        stacks.append("JavaScript/Node.js")
    if any(Path(path).name.lower().startswith("tsconfig") for path in paths) or any(path.endswith((".ts", ".tsx")) for path in lower_paths):
        stacks.append("TypeScript")
    if any(Path(path).name.lower() in {"pyproject.toml", "requirements.txt", "setup.py", "setup.cfg"} for path in paths) or any(path.endswith(".py") for path in lower_paths):
        stacks.append("Python")
    if "composer.json" in basenames or any(path.endswith(".php") for path in lower_paths):
        stacks.append("PHP")
    if "go.mod" in basenames:
        stacks.append("Go")
    if "cargo.toml" in basenames:
        stacks.append("Rust")
    if any(Path(path).name.lower() in {"pom.xml", "build.gradle", "build.gradle.kts"} for path in paths):
        stacks.append("JVM")
    if any(Path(path).name.lower() == "gemfile" for path in paths):
        stacks.append("Ruby")
    if any(path.endswith((".csproj", ".sln")) for path in lower_paths):
        stacks.append(".NET")
    if "package.swift" in basenames:
        stacks.append("Swift")
    if "mix.exs" in basenames:
        stacks.append("Elixir")
    if "pubspec.yaml" in basenames:
        stacks.append("Dart/Flutter")
    if any(Path(path).name.lower().startswith("dockerfile") for path in paths):
        stacks.append("Container")
    return sorted(set(stacks))


def read_package_scripts(
    root: Path,
    paths: Sequence[str],
    max_items: int,
) -> List[Dict[str, Any]]:
    packages: List[Dict[str, Any]] = []
    candidates = [
        path
        for path in paths
        if Path(path).name == "package.json" and path_depth(path) <= 4
    ][:max_items]
    for relative in candidates:
        full_path = root / relative
        try:
            if full_path.is_symlink():
                raise OSError("拒绝读取符号链接")
            full_path.resolve(strict=True).relative_to(root.resolve())
            with full_path.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, ValueError, UnicodeDecodeError, json.JSONDecodeError):
            packages.append({"path": relative, "error": "无法解析 package.json"})
            continue
        raw_scripts = data.get("scripts") if isinstance(data, dict) else None
        scripts: List[Dict[str, str]] = []
        if isinstance(raw_scripts, dict):
            names = [name for name in raw_scripts if RELEVANT_SCRIPT_RE.search(str(name))]
            for name in sorted(names)[:max_items]:
                scripts.append({"name": str(name)[:100]})
        engines = data.get("engines") if isinstance(data, dict) else None
        safe_engines = {}
        if isinstance(engines, dict):
            for key in ("node", "npm", "pnpm", "yarn", "bun"):
                value = engines.get(key)
                if isinstance(value, str) and re.fullmatch(r"[0-9A-Za-z.*+<>=~^| -]{1,100}", value):
                    safe_engines[key] = value
        package_manager = data.get("packageManager") if isinstance(data, dict) else None
        if not isinstance(package_manager, str) or not re.fullmatch(
            r"(?:npm|pnpm|yarn|bun)@[0-9A-Za-z.+_-]{1,160}", package_manager
        ):
            package_manager = None
        packages.append(
            {
                "path": relative,
                "scripts": scripts,
                "engines": safe_engines,
                "package_manager": package_manager if isinstance(package_manager, str) else None,
            }
        )
    return packages


def git_report(root: Path) -> Dict[str, Any]:
    inside = git_value(root, ["rev-parse", "--is-inside-work-tree"]) == "true"
    if not inside:
        return {"is_repository": False}

    commit = git_value(root, ["rev-parse", "HEAD"])
    branch = git_value(root, ["symbolic-ref", "--quiet", "--short", "HEAD"]) or "DETACHED"
    upstream = git_value(root, ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"])
    status_text = git_value(root, ["status", "--porcelain=v1", "--untracked-files=normal"]) or ""
    dirty_entries = [line for line in status_text.splitlines() if line]
    ahead = None
    behind = None
    if upstream:
        counts = git_value(root, ["rev-list", "--left-right", "--count", "HEAD...@{u}"])
        if counts:
            parts = counts.split()
            if len(parts) == 2 and all(part.isdigit() for part in parts):
                ahead, behind = int(parts[0]), int(parts[1])
    return {
        "is_repository": True,
        "branch": branch,
        "commit": commit,
        "upstream": upstream,
        "ahead": ahead,
        "behind": behind,
        "dirty_count": len(dirty_entries),
        "dirty_entries": dirty_entries[:80],
        "dirty_entries_truncated": len(dirty_entries) > 80,
        "remote_refs_refreshed": False,
    }


def keyword_signals(paths: Sequence[str], keyword: str, max_items: int) -> List[str]:
    pattern = re.compile(rf"(^|[/_.-]){re.escape(keyword)}([/_.-]|$)", re.IGNORECASE)
    return limited((path for path in paths if pattern.search(path)), max_items)


def build_report(
    requested_root: Path,
    max_files: int,
    max_items: int,
) -> Dict[str, Any]:
    git_root = git_value(requested_root, ["rev-parse", "--show-toplevel"])
    root = Path(git_root).resolve() if git_root else requested_root.resolve()
    paths, scan_truncated = scan_files(root, max_files)

    instructions = find_instruction_files(paths, max_items)
    ci_files = limited((path for path in paths if is_ci_file(path)), max_items)
    delivery_files = limited((path for path in paths if is_delivery_file(path)), max_items)
    test_files = limited((path for path in paths if is_test_file(path)), max_items)
    migration_files = limited((path for path in paths if is_migration_file(path)), max_items)
    manifests = limited((path for path in paths if Path(path).name.lower() in MANIFEST_NAMES), max_items)
    lockfiles = limited((path for path in paths if Path(path).name.lower() in LOCK_NAMES), max_items)
    runtime_pins = limited((path for path in paths if Path(path).name.lower() in RUNTIME_PIN_NAMES), max_items)
    config_templates = limited((path for path in paths if Path(path).name.lower() in CONFIG_TEMPLATE_NAMES), max_items)
    package_scripts = read_package_scripts(root, paths, max_items)
    git = git_report(root)

    signal_names = ["health", "smoke", "rollback", "backup", "restore", "deploy", "release", "monitor", "migrate"]
    signals = {name: keyword_signals(paths, name, max_items) for name in signal_names}

    warnings: List[str] = []
    if git.get("is_repository"):
        if git.get("dirty_count"):
            warnings.append(f"工作树存在 {git['dirty_count']} 条变化；先保护并区分用户改动。")
        if not git.get("upstream"):
            warnings.append("当前分支没有 upstream；无法证明提交已进入远端发布基线。")
        if isinstance(git.get("behind"), int) and git["behind"] > 0:
            warnings.append(f"当前 HEAD 基于本地远端引用落后 upstream {git['behind']} 个提交；探针未执行 fetch。")
        if isinstance(git.get("ahead"), int) and git["ahead"] > 0:
            warnings.append(f"当前 HEAD 领先 upstream {git['ahead']} 个提交；发布前确认已 push。")
    else:
        warnings.append("未检测到 Git 仓库；若需要发布，先建立可追溯源码基线。")

    scoped_instructions = [path for path in instructions if Path(path).name.lower() in {"agents.md", "claude.md", "contributing.md", "contributing"}]
    if not scoped_instructions:
        warnings.append("未发现 AGENTS/CLAUDE/CONTRIBUTING 指令；仍需人工读取 README 与运维资料。")
    if not ci_files:
        warnings.append("未发现常见 CI 配置；可能需要人工确认或建立最低 CI 基线。")
    if not test_files and not any(package.get("scripts") for package in package_scripts):
        warnings.append("未发现明显测试文件或测试脚本；不要据此断言项目没有测试。")
    if not delivery_files:
        warnings.append("未发现明显发布/基础设施文件；部署目标与回滚方式仍未知。")
    if migration_files and not signals["backup"]:
        warnings.append("发现 migration/schema 线索，但未从文件名发现备份入口；必须人工核对恢复方案。")
    if delivery_files and not (signals["health"] or signals["smoke"]):
        warnings.append("发现交付线索，但未从文件名发现 health/smoke 入口；必须人工核对发布后验证。")
    if delivery_files and not signals["rollback"]:
        warnings.append("发现交付线索，但未从文件名发现 rollback 入口；必须人工核对恢复路径。")
    js_locks = {Path(path).name.lower() for path in lockfiles if Path(path).name.lower() in {"bun.lock", "bun.lockb", "package-lock.json", "pnpm-lock.yaml", "yarn.lock"}}
    if len(js_locks) > 1:
        warnings.append(f"检测到多种 JavaScript 锁文件：{', '.join(sorted(js_locks))}；确认每个工作区的包管理器。")
    if scan_truncated:
        warnings.append(f"扫描在 {max_files} 个文件处截断；结果可能遗漏深层线索。")

    return {
        "probe_version": VERSION,
        "requested_root": str(requested_root.resolve()),
        "project_root": str(root),
        "scan": {"file_count": len(paths), "truncated": scan_truncated, "max_files": max_files},
        "git": git,
        "stacks": detect_stacks(paths),
        "instructions": instructions,
        "manifests": manifests,
        "lockfiles": lockfiles,
        "runtime_pins": runtime_pins,
        "config_templates": config_templates,
        "package_scripts": package_scripts,
        "ci_files": ci_files,
        "delivery_files": delivery_files,
        "test_files": test_files,
        "migration_files": migration_files,
        "signals": signals,
        "warnings": warnings,
        "limitations": [
            "结果仅基于文件名、Git 元数据和 package.json 的脚本名/engines 启发式生成。",
            "探针不执行 fetch、不访问网络或生产、不读取 .env/秘密正文，也不执行项目脚本。",
            "所有命令、部署目标、健康检查和回滚方式都必须回到项目原文件与目标环境逐项核实。",
        ],
    }


def markdown_escape(value: Any) -> str:
    text = str(value).replace("`", "'").replace("|", "\\|")
    return " ".join(text.split())


def markdown_list(title: str, items: Sequence[str]) -> List[str]:
    lines = [f"## {title}", ""]
    if not items:
        lines.append("- 未发现明显线索")
    else:
        lines.extend(f"- `{markdown_escape(item)}`" for item in items)
    lines.append("")
    return lines


def render_markdown(report: Dict[str, Any]) -> str:
    git = report["git"]
    lines = [
        "# 项目交付只读探测",
        "",
        "> 这是启发式索引，不是执行计划；逐项回到仓库和目标环境核实。",
        "",
        "## 基线",
        "",
        "| 项 | 值 |",
        "|---|---|",
        f"| 项目根 | `{markdown_escape(report['project_root'])}` |",
        f"| 扫描文件 | {report['scan']['file_count']}{'（已截断）' if report['scan']['truncated'] else ''} |",
        f"| 技术栈信号 | {markdown_escape(', '.join(report['stacks']) or '未识别')} |",
        f"| Git | {'是' if git.get('is_repository') else '否'} |",
    ]
    if git.get("is_repository"):
        lines.extend(
            [
                f"| branch | `{markdown_escape(git.get('branch'))}` |",
                f"| commit | `{markdown_escape(git.get('commit'))}` |",
                f"| upstream | `{markdown_escape(git.get('upstream') or '未设置')}` |",
                f"| ahead / behind | {markdown_escape(git.get('ahead'))} / {markdown_escape(git.get('behind'))}（基于本地远端引用） |",
                f"| 工作树变化 | {git.get('dirty_count', 0)} |",
            ]
        )
    lines.append("")

    if git.get("dirty_entries"):
        lines.extend(["## 工作树变化（最多 80 条）", ""])
        lines.extend(f"- `{markdown_escape(entry)}`" for entry in git["dirty_entries"])
        lines.append("")

    lines.extend(markdown_list("项目指令与入口文档", report["instructions"]))
    lines.extend(
        markdown_list(
            "清单、锁文件与配置模板",
            report["manifests"] + report["lockfiles"] + report["runtime_pins"] + report["config_templates"],
        )
    )

    lines.extend(["## 相关 package scripts", ""])
    if not report["package_scripts"]:
        lines.append("- 未发现可解析的 package.json")
    for package in report["package_scripts"]:
        lines.append(f"- `{markdown_escape(package['path'])}`")
        if package.get("error"):
            lines.append(f"  - {markdown_escape(package['error'])}")
            continue
        if package.get("engines"):
            engines = ", ".join(f"{key}={value}" for key, value in package["engines"].items())
            lines.append(f"  - engines: `{markdown_escape(engines)}`")
        if package.get("package_manager"):
            lines.append(f"  - packageManager: `{markdown_escape(package['package_manager'])}`")
        scripts = package.get("scripts", [])
        if not scripts:
            lines.append("  - 未发现相关脚本名")
        for script in scripts:
            lines.append(f"  - `{markdown_escape(script['name'])}`")
    lines.append("")

    lines.extend(markdown_list("CI", report["ci_files"]))
    lines.extend(markdown_list("发布与基础设施", report["delivery_files"]))
    lines.extend(markdown_list("测试线索（截取）", report["test_files"]))
    lines.extend(markdown_list("迁移与 schema 线索（截取）", report["migration_files"]))

    lines.extend(["## 风险提示", ""])
    if report["warnings"]:
        lines.extend(f"- {markdown_escape(item)}" for item in report["warnings"])
    else:
        lines.append("- 未产生启发式警告；仍需人工核实。")
    lines.append("")

    lines.extend(["## 探针边界", ""])
    lines.extend(f"- {markdown_escape(item)}" for item in report["limitations"])
    lines.append("")
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".", help="要探测的项目目录，默认当前目录")
    parser.add_argument("--format", choices=("markdown", "json"), default="markdown")
    parser.add_argument("--max-files", type=int, default=60000, help="最多扫描文件数")
    parser.add_argument("--max-items", type=int, default=30, help="每类最多输出条目数")
    parser.add_argument("--version", action="version", version=VERSION)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = Path(args.root).expanduser()
    if not root.exists() or not root.is_dir():
        print(f"错误：项目目录不存在或不是目录：{root}", file=sys.stderr)
        return 2
    if args.max_files <= 0 or args.max_items <= 0:
        print("错误：--max-files 与 --max-items 必须为正整数", file=sys.stderr)
        return 2

    report = build_report(root, args.max_files, args.max_items)
    if args.format == "json":
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(render_markdown(report), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
