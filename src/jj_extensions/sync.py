from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from .shell import run, run_ok, capture_json


STACK_SECTION_TEMPLATE = (
    "<!-- {key}:start -->\n"  # managed section start
    "{lines}\n"
    "<!-- {key}:end -->"
)


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
    # Try gh first
    try:
        data = capture_json(
            ["gh", "repo", "view", "--json", "defaultBranchRef"], cwd=repo_path
        )
        default_ref = data.get("defaultBranchRef", {})
        name = default_ref.get("name")
        if name:
            return name
    except Exception:
        pass
    # Fallback to git
    try:
        ref = run_ok(["git", "symbolic-ref", "refs/remotes/origin/HEAD"], cwd=repo_path)
        return ref.rsplit("/", 1)[-1]
    except Exception:
        return "main"


def list_branches(repo_path: str) -> List[BranchInfo]:
    # Use jj branch list in JSON if available; otherwise parse text safely
    try:
        data = capture_json(["jj", "branch", "list", "--json"], cwd=repo_path)
        branches: List[BranchInfo] = []
        for item in data:
            name = item.get("name") or item.get("names", [None])[0]
            target = (item.get("local_target") or item.get("target") or {}).get("id")
            if name and target:
                branches.append(BranchInfo(name=name, target=target))
        return branches
    except Exception:
        text = run_ok(["jj", "branch", "list"], cwd=repo_path)
        branches: List[BranchInfo] = []
        for line in text.splitlines():
            parts = line.strip().split()
            if not parts:
                continue
            name = parts[0]
            # target hash often appears like @<shortid>; we can resolve later
            branches.append(BranchInfo(name=name, target=""))
        return branches


def sort_branches_as_stack(repo_path: str, branches: List[BranchInfo]) -> List[str]:
    # Derive a linear stack based on ancestry by jj order of revset: reachable from @ sorted by topological order
    # Map branch to the youngest-first order using jj log over bookmarks
    names = [b.name for b in branches]
    if not names:
        return []
    # Use revset of these bookmarks and order by topo so parents appear before children
    template = "{bookmarks|join(',')} {commit_id}\n"
    revset = " or ".join([f"branch({name})" for name in names])
    out = run_ok(
        ["jj", "log", "-r", revset, "--no-graph", "-T", template], cwd=repo_path
    )
    lines = [l for l in out.splitlines() if l.strip()]
    # Build map commit->branches listed on it
    commit_to_branches: List[Tuple[str, List[str]]] = []
    for line in lines:
        try:
            left, commit = line.rsplit(" ", 1)
            bnames = [n for n in left.split(",") if n]
            commit_to_branches.append((commit, bnames))
        except ValueError:
            continue
    # Preserve topo order; for each commit, emit branches on it in listed order
    ordered: List[str] = []
    seen = set()
    for _, bnames in commit_to_branches:
        for b in bnames:
            if b in names and b not in seen:
                ordered.append(b)
                seen.add(b)
    # If any branches were missing (no direct commit line), append them
    for b in names:
        if b not in seen:
            ordered.append(b)
    return ordered


def gh_list_open_prs_by_head(repo_path: str) -> Dict[str, PullRequest]:
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
    # gh prints URL; extract trailing number
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
    # 1) Push; abort on failure
    run(
        ["jj", "git", "push", "--allow-new", "--remote", remote],
        cwd=repo_path,
        check=True,
    )

    # 2) Discover branches and ordering
    branches = list_branches(repo_path)
    if not branches:
        return
    ordered_branch_names = sort_branches_as_stack(repo_path, branches)

    # Default base branch
    base_default = default_base or get_default_branch(repo_path)

    # 3) Load existing PRs by head
    head_to_pr = gh_list_open_prs_by_head(repo_path)

    # 4) Ensure PRs exist and bases are correct; collect PR numbers in order
    pr_numbers_in_order: List[int] = []
    for idx, branch_name in enumerate(ordered_branch_names):
        base = base_default if idx == 0 else ordered_branch_names[idx - 1]
        pr = head_to_pr.get(branch_name)
        if pr is None:
            title = branch_name
            body = ""
            if dry_run:
                # Simulate PR number as 0 placeholder
                pr_num = 0
            else:
                pr_num = gh_create_pr(
                    repo_path, head=branch_name, base=base, title=title, body=body
                )
            pr_numbers_in_order.append(pr_num)
            if pr_num:
                head_to_pr[branch_name] = PullRequest(
                    number=pr_num, head=branch_name, base=base, body=""
                )
        else:
            pr_numbers_in_order.append(pr.number)
            # Update base if needed
            if pr.base != base and not dry_run:
                gh_update_pr(repo_path, pr.number, base=base, body=None)

    # 5) Update PR bodies with marker section list
    for idx, branch_name in enumerate(ordered_branch_names):
        pr = head_to_pr.get(branch_name)
        if not pr:
            continue
        section = render_stack_section(marker_key, pr_numbers_in_order, idx)
        new_body = upsert_marker_section(pr.body or "", marker_key, section)
        if not dry_run:
            gh_update_pr(repo_path, pr.number, base=None, body=new_body)
