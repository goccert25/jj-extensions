from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, List, Optional

from .shell import run, run_ok


STACK_SECTION_TEMPLATE = "<!-- {key}:start -->\n{lines}\n<!-- {key}:end -->"


@dataclass
class BranchInfo:
    name: str
    target: str  # commit id


@dataclass
class PullRequest:
    number: int
    head: str
    base: str
    body: str


def get_default_branch(repo_path: str) -> str:
    try:
        # Use gh if available
        from .shell import (
            capture_json,
        )  # local import to avoid unused when gh not present

        data = capture_json(
            ["gh", "repo", "view", "--json", "defaultBranchRef"], cwd=repo_path
        )
        default_ref = data.get("defaultBranchRef", {})
        name = default_ref.get("name")
        if name:
            return name
    except Exception:
        pass
    try:
        ref = run_ok(["git", "symbolic-ref", "refs/remotes/origin/HEAD"], cwd=repo_path)
        return ref.rsplit("/", 1)[-1]
    except Exception:
        return "main"


def _sanitize_branch_name(raw: str) -> Optional[str]:
    name = raw.strip()
    if not name:
        return None
    # Ignore remote markers like "@origin/..."
    if name.startswith("@"):
        return None
    # Drop trailing colon that jj sometimes shows after names
    if name.endswith(":"):
        name = name[:-1]
    # If there are multiple branches on one commit, just get the first one
    if len(name.split(" ")) >= 2:
        name = name.split(" ")[0]
    return name or None


def get_branches_from_main_to_current_commit_excluding_main(
    repo_path: str,
) -> List[str]:
    # Prefer templated output for broad jj compatibility
    try:
        out = run_ok(
            # jj log -r 'trunk()..@' -T 'bookmarks++"\n"' --no-graph
            ["jj", "log", "-r", "trunk()..@", "-T", 'bookmarks++"\\n"', "--no-graph"],
            cwd=repo_path,
        )
        # branches: List[BranchInfo] = []
        branches: List[str] = []
        for line in out.splitlines():
            name = _sanitize_branch_name(line)
            if name:
                # branches.append(BranchInfo(name=name, target=""))
                branches.append(name)
        if branches:
            branches.reverse()
            return branches
    except Exception:
        pass


def _quote_revset_string(s: str) -> str:
    escaped = s.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def gh_list_open_prs_by_head(repo_path: str) -> Dict[str, PullRequest]:
    from .shell import capture_json

    data = capture_json(
        [
            "gh",
            "pr",
            "list",
            "--state",
            "open",
            "--json",
            "number,headRefName,baseRefName,body",
        ],
        cwd=repo_path,
    )
    result: Dict[str, PullRequest] = {}
    for pr in data:
        number = int(pr["number"])
        head = pr["headRefName"]
        base = pr.get("baseRefName", "")
        body = pr.get("body") or ""
        result[head] = PullRequest(number=number, head=head, base=base, body=body)
    return result


def gh_create_pr(repo_path: str, head: str, base: str, title: str, body: str) -> int:
    out = run_ok(
        [
            "gh",
            "pr",
            "create",
            "--head",
            head,
            "--base",
            base,
            "--title",
            title,
            "--body",
            body,
        ],
        cwd=repo_path,
    )
    m = re.search(r"/(\d+)$", out.strip())
    if not m:
        raise RuntimeError(f"Failed to get PR number from gh output: {out}")
    return int(m.group(1))


def gh_update_pr(
    repo_path: str, number: int, base: Optional[str], body: Optional[str]
) -> None:
    args = ["gh", "pr", "edit", str(number)]
    if base:
        args += ["--base", base]
    if body is not None:
        args += ["--body", body]
    run_ok(args, cwd=repo_path)


def render_stack_section(
    marker_key: str, pr_numbers_in_order: List[int], current_index: int
) -> str:
    lines: List[str] = []
    for i, num in enumerate(pr_numbers_in_order):
        line = f"- {'👉 ' if i == current_index else ''}#{num}"
        lines.append(line)
    return STACK_SECTION_TEMPLATE.format(key=marker_key, lines="\n".join(lines))


def upsert_marker_section(existing_body: str, marker_key: str, new_section: str) -> str:
    start = f"<!-- {marker_key}:start -->"
    end = f"<!-- {marker_key}:end -->"
    if start in existing_body and end in existing_body:
        before = existing_body.split(start, 1)[0].rstrip()
        after = existing_body.split(end, 1)[1].lstrip()
        combined = before + "\n\n" + new_section + ("\n\n" + after if after else "")
        return combined.strip()
    else:
        if existing_body.strip():
            return (new_section + "\n\n" + existing_body.strip()).strip()
        else:
            return new_section


def sync_stack(
    repo_path: str,
    remote: str = "origin",
    default_base: Optional[str] = None,
    marker_key: str = "jj-stack-sync",
    dry_run: bool = False,
) -> None:
    run(
        # jj git push -r 'trunk()..@' --allow-new
        ["jj", "git", "push", "-r", "trunk()..@", "--allow-new"],
        cwd=repo_path,
        check=True,
    )

    branches = get_branches_from_main_to_current_commit_excluding_main(repo_path)
    if not branches:
        return
    print(branches)
    base_default = default_base or get_default_branch(repo_path)
    print(base_default)
    head_to_pr = gh_list_open_prs_by_head(repo_path)
    print(head_to_pr)

    pr_numbers_in_order: List[int] = []
    for idx, branch_name in enumerate(branches):
        base = base_default if idx == 0 else branches[idx - 1]
        pr = head_to_pr.get(branch_name)
        if pr is None:
            title = branch_name
            body = ""
            if dry_run:
                pr_num = 0
            else:
                print(f"Creating PR for {branch_name} to {base}")
                pr_num = gh_create_pr(
                    repo_path, head=branch_name, base=base, title=title, body=body
                )
                print(f"PR created: {pr_num}")
            pr_numbers_in_order.append(pr_num)
            if pr_num:
                print(f"Adding PR to head_to_pr: {branch_name} -> {pr_num}")
                head_to_pr[branch_name] = PullRequest(
                    number=pr_num, head=branch_name, base=base, body=""
                )
        else:
            print(f"Updating PR for {branch_name} to {base}")
            pr_numbers_in_order.append(pr.number)
            if pr.base != base and not dry_run:
                print(f"Updating PR for {branch_name} to {base}")
                gh_update_pr(repo_path, pr.number, base=base, body=None)
                print(f"PR updated: {pr.number}")

    for idx, branch_name in enumerate(branches):
        pr = head_to_pr.get(branch_name)
        print(f"pr: {pr}")
        if not pr:
            continue
        section = render_stack_section(marker_key, pr_numbers_in_order, idx)
        print(f"section: {section}")
        new_body = upsert_marker_section(pr.body or "", marker_key, section)
        print(f"new_body: {new_body}")
        if not dry_run:
            print(f"Updating PR body for {branch_name}")
            gh_update_pr(repo_path, pr.number, base=None, body=new_body)
