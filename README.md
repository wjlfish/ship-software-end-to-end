# Ship Software End to End

[![验证 Skill](https://github.com/wjlfish/ship-software-end-to-end/actions/workflows/validate.yml/badge.svg)](https://github.com/wjlfish/ship-software-end-to-end/actions/workflows/validate.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

一个同时面向 Codex 与 Claude Code 的通用软件全周期交付 Skill。它遵循双方支持的 Agent Skills `SKILL.md` 结构，把需求对齐、代码与生产基线、根因定位、最小实现、风险自适应测试、CI、不可变制品、部署、烟测、回滚、观察和交接串成一条可验证的闭环。

它不绑定语言、框架、云厂商或分支模型，而是优先从目标项目的仓库指令、脚本、CI 和运行环境中发现真实交付方式。

## 能解决什么

- 接手陌生项目并建立代码、运行和发布基线
- 实现功能或修复 Bug，并找全入口、写点、异步消费者和旁路
- 根据鉴权、数据、资金、并发、迁移和外部副作用调整验证强度
- 从同一 commit 构建并晋升不可变制品
- 完成部署、版本复核、烟测、观察和可执行回滚
- 在用户只要求说明、评审或诊断时严格保持只读，不擅自扩张为发布
- 用户在执行中明确催促时，冻结可选范围、保留最低硬闸门并走最短完成路径，不以半成品交差

## 安装

### Codex：使用 Skill Installer

在 Codex 中输入：

```text
$skill-installer 从 https://github.com/wjlfish/ship-software-end-to-end/tree/v1.3.0/skills/ship-software-end-to-end 安装这个 Skill
```

### Codex 与 Claude Code：共用一个本地仓库

Codex 从 `$HOME/.agents/skills` 发现用户级 Skill，Claude Code 从 `$HOME/.claude/skills` 发现个人 Skill。以下方式只保留一份 Git 仓库，并让两个工具指向同一份 Skill 源码：

```bash
git clone --branch v1.3.0 --depth 1 \
  https://github.com/wjlfish/ship-software-end-to-end.git \
  "$HOME/.local/share/ship-software-end-to-end"
mkdir -p "$HOME/.agents/skills" "$HOME/.claude/skills"
ln -s "$HOME/.local/share/ship-software-end-to-end/skills/ship-software-end-to-end" \
  "$HOME/.agents/skills/ship-software-end-to-end"
ln -s "$HOME/.local/share/ship-software-end-to-end/skills/ship-software-end-to-end" \
  "$HOME/.claude/skills/ship-software-end-to-end"
```

Codex 显式调用名为 `$ship-software-end-to-end`，Claude Code 显式调用名为 `/ship-software-end-to-end`；两者也都可根据 `description` 自动加载。若工具没有立即发现新建的顶层目录，重启对应客户端。上面的精确标签保证安装内容可复现；`main` 分支用于查看下一版变更。目录约定与调用方式参见 [OpenAI 官方 Skill 文档](https://learn.chatgpt.com/docs/build-skills)和 [Claude Code 官方 Skill 文档](https://code.claude.com/docs/en/slash-commands)。

## 使用

Codex 显式调用：

```text
使用 $ship-software-end-to-end 实现这个功能，完成测试并发布到生产。
```

Claude Code 显式调用：

```text
/ship-software-end-to-end 实现这个功能，完成测试并发布到生产。
```

也可以直接提出与描述匹配的自然语言任务，例如：

```text
接手这个项目，把登录故障定位、修好、走完 CI 并上线，最后确认回滚点。
```

Skill 会先判断授权终点。没有发布授权时，只完成用户允许的调查、实现或验证范围。

## 仓库结构

```text
skills/ship-software-end-to-end/
├── SKILL.md
├── agents/openai.yaml
├── references/
└── scripts/project_delivery_probe.py
```

Skill 包本身保持精简；仓库根目录只承载公开说明、许可证和持续验证。

## 只读项目探针

首次进入陌生项目时，可以使用 Python 3.9 或更高版本运行：

```bash
python3 skills/ship-software-end-to-end/scripts/project_delivery_probe.py \
  --root /path/to/project
```

探针只根据文件名、Git 元数据和安全的项目清单字段生成启发式索引。它不会执行 `fetch`、访问生产、读取 `.env` 或秘密正文，也不会运行项目脚本。

JSON 输出：

```bash
python3 skills/ship-software-end-to-end/scripts/project_delivery_probe.py \
  --root /path/to/project \
  --format json
```

## 验证

```bash
python3 tests/validate_skill.py
```

验证覆盖 Skill 元数据、引用完整性、公开信息扫描、Python 语法、探针 Markdown/JSON 输出、Git 状态识别以及符号链接和脚本正文隔离。

## 许可证

[MIT](LICENSE)
