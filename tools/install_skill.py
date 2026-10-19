#!/usr/bin/env python3
"""Install a discoverable user skill without changing global MCP configuration."""
import argparse
import os
from pathlib import Path


def install(source, skills_dir):
    source, skills_dir = source.resolve(), skills_dir.expanduser().resolve()
    if not (source / "SKILL.md").is_file():
        raise ValueError("技能源目录中缺少 SKILL.md")
    target = skills_dir / "phonepilot"
    if os.path.lexists(target):
        if target.is_symlink() and target.resolve() == source:
            return target, False
        raise ValueError("目标位置已有同名技能；未覆盖，请检查原技能或指定 --skills-dir。")
    skills_dir.mkdir(parents=True, exist_ok=True)
    target.symlink_to(source, target_is_directory=True)
    return target, True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skills-dir", type=Path, default=Path.home() / ".agents/skills")
    args = parser.parse_args()
    root = Path(__file__).resolve().parent.parent
    try:
        target, created = install(root / "skills/phonepilot", args.skills_dir)
    except (ValueError, OSError) as exc:
        parser.exit(1, f"安装未完成：{exc}\n")
    print(f"{'已安装' if created else '已存在，无需重复安装'}：{target}")
    print("以后可直接说：用 PhonePilot 打开手机侧栏。")
    print("技能需要此项目留在当前目录；项目移动后请更新技能链接。")


if __name__ == "__main__":
    main()
