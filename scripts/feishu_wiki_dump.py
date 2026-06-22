"""Dump a Feishu wiki space (knowledge base) to text — D4 第③段 知识反哺地基.

Read-only. Walks the wiki node tree and prints each node's title + plain-text
content. This is the access foundation the knowledge-flywheel skill will use to
「与存量库语义核对」; it is NOT the flywheel itself (no write, no quadrant logic).

Requires the Feishu app to have wiki:wiki(:readonly) + docx:document:readonly,
and FEISHU_APP_ID / FEISHU_APP_SECRET in backend/.env.

Usage (from repo root or backend/, with backend venv):
    backend/.venv/bin/python scripts/feishu_wiki_dump.py --space 7641622607627816151
    # subtree under one node:
    backend/.venv/bin/python scripts/feishu_wiki_dump.py --space <id> --root <node_token>
    # structure only (no doc content fetch):
    backend/.venv/bin/python scripts/feishu_wiki_dump.py --space <id> --tree-only
    # JSON output (for downstream tooling):
    backend/.venv/bin/python scripts/feishu_wiki_dump.py --space <id> --json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent  # scripts/
REPO_ROOT = SCRIPT_DIR.parent  # ticket-hub/
BACKEND_DIR = REPO_ROOT / "backend"
sys.path.insert(0, str(BACKEND_DIR))


def main() -> int:
    ap = argparse.ArgumentParser(description="Dump a Feishu wiki space to text.")
    ap.add_argument("--space", required=True, help="wiki space_id")
    ap.add_argument("--root", default=None, help="optional root node_token (subtree)")
    ap.add_argument("--tree-only", action="store_true", help="structure only, skip doc content")
    ap.add_argument("--json", action="store_true", help="emit JSON instead of text")
    ap.add_argument("--max-chars", type=int, default=4000, help="per-doc content cap (text mode)")
    args = ap.parse_args()

    from adapters.feishu import FeishuClient, FeishuConfig
    from app.config import get_settings

    settings = get_settings()
    if not (settings.feishu_app_id and settings.feishu_app_secret):
        print("ERROR: FEISHU_APP_ID / FEISHU_APP_SECRET not configured", file=sys.stderr)
        return 2

    client = FeishuClient(FeishuConfig.from_settings(settings))
    try:
        nodes = client.walk_wiki_tree(args.space, root_node_token=args.root)
    except Exception as e:  # surface the scope/permission error clearly
        print(f"ERROR walking wiki tree: {e}", file=sys.stderr)
        client.close()
        return 1

    # depth via parent chain (for indentation)
    token_to_node = {n.node_token: n for n in nodes}

    def depth(n) -> int:  # type: ignore[no-untyped-def]
        d, cur = 0, n
        while cur.parent_node_token and cur.parent_node_token in token_to_node:
            d += 1
            cur = token_to_node[cur.parent_node_token]
        return d

    records = []
    for n in nodes:
        content = ""
        if not args.tree_only and n.obj_type == "docx" and n.obj_token:
            try:
                content = client.get_doc_raw_content(n.obj_token)
            except Exception as e:
                content = f"<读取失败: {e}>"
        records.append(
            {
                "node_token": n.node_token,
                "obj_token": n.obj_token,
                "obj_type": n.obj_type,
                "title": n.title,
                "depth": depth(n),
                "has_child": n.has_child,
                "content": content,
            }
        )
    client.close()

    if args.json:
        print(json.dumps(records, ensure_ascii=False, indent=2))
        return 0

    print(f"# 飞书知识库 space={args.space}  共 {len(records)} 个节点\n")
    for r in records:
        indent = "  " * r["depth"]
        print(f"{indent}- {r['title']}  [{r['obj_type']}] ({r['node_token']})")
        if r["content"]:
            body = r["content"].strip()
            if len(body) > args.max_chars:
                body = body[: args.max_chars] + f"\n… (截断，共 {len(r['content'])} 字)"
            for line in body.splitlines():
                print(f"{indent}    {line}")
            print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
