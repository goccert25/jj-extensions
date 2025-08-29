from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

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
    return name or None


def list_branches(repo_path: str) -> List[BranchInfo]:
    # Prefer templated output for broad jj compatibility
    try:
        out = run_ok(
            ["jj", "bookmark", "list", "-T", 'name++"\\n"'],
            cwd=repo_path,
        )
        branches: List[BranchInfo] = []
        for line in out.splitlines():
            name = _sanitize_branch_name(line)
            if name:
                branches.append(BranchInfo(name=name, target=""))
        if branches:
            return branches
    except Exception:
        pass
    # Last resort: plain text parsing
    text = run_ok(["jj", "bookmark", "list"], cwd=repo_path)
    branches: List[BranchInfo] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        parts = line.strip().split()
        name_raw = parts[0]
        name = _sanitize_branch_name(name_raw)
        if name:
            branches.append(BranchInfo(name=name, target=""))
    return branches


def _quote_revset_string(s: str) -> str:
    escaped = s.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def sort_branches_as_stack(repo_path: str, branches: List[BranchInfo]) -> List[str]:
    # Dedupe while preserving original order
    seen = set()
    names: List[str] = []
    for b in branches:
        if b.name not in seen:
            names.append(b.name)
            seen.add(b.name)
    if not names:
        return []
    # Template: bookmarks joined by comma, then space, then commit_id, newline
    template = 'bookmarks.join(",") ++ " " ++ commit_id ++ "\\n"'
    # Use bookmarks() to select the commits pointed at by these bookmarks
    rev_terms = [f"bookmarks({_quote_revset_string(name)})" for name in names]
    revset = " | ".join(rev_terms)
    out = run_ok(
        ["jj", "log", "-r", revset, "--no-graph", "-T", template], cwd=repo_path
    )
    lines = [l for l in out.splitlines() if l.strip()]
    commit_to_branches: List[Tuple[str, List[str]]] = []
    for line in lines:
        try:
            left, commit = line.rsplit(" ", 1)
            bnames = [n for n in left.split(",") if n]
            commit_to_branches.append((commit, bnames))
        except ValueError:
            continue
    ordered: List[str] = []
    emitted = set()
    for _, bnames in commit_to_branches:
        for b in bnames:
            if b in names and b not in emitted:
                ordered.append(b)
                emitted.add(b)
    for b in names:
        if b not in emitted:
            ordered.append(b)
    return ordered


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
        ["jj", "git", "push", "--allow-new", "--remote", remote],
        cwd=repo_path,
        check=True,
    )

    branches = list_branches(repo_path)
    if not branches:
        return
    ordered_branch_names = sort_branches_as_stack(repo_path, branches)
    print(ordered_branch_names)
    base_default = default_base or get_default_branch(repo_path)
    print(base_default)
    head_to_pr = gh_list_open_prs_by_head(repo_path)
    print(head_to_pr)

    pr_numbers_in_order: List[int] = []
    for idx, branch_name in enumerate(ordered_branch_names):
        base = base_default if idx == 0 else ordered_branch_names[idx - 1]
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

    for idx, branch_name in enumerate(ordered_branch_names):
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
