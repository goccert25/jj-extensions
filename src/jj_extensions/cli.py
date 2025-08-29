import sys
import subprocess
from typing import Optional

import click

from .sync import sync_stack


@click.group()
@click.version_option()
@click.option(
    "--repo",
    type=click.Path(file_okay=False, dir_okay=True),
    default=".",
    help="TEST Path to repo (default: .)",
)
@click.pass_context
def main(ctx: click.Context, repo: str) -> None:
    print("main")
    ctx.ensure_object(dict)
    ctx.obj["repo"] = repo


@main.group()
@click.pass_context
def stack(ctx: click.Context) -> None:
    pass


@stack.command("sync")
@click.option("--remote", default="origin", help="Git remote to use (default: origin)")
@click.option(
    "--default-base",
    default=None,
    help="Default base branch (fallback to repo default branch)",
)
@click.option(
    "--marker", default="jj-stack-sync", help="Marker key to manage in PR bodies"
)
@click.option("--dry-run", is_flag=True, help="Show actions without making changes")
@click.pass_context
def stack_sync(
    ctx: click.Context,
    remote: str,
    default_base: Optional[str],
    marker: str,
    dry_run: bool,
) -> None:
    print("stack_sync")
    repo = ctx.obj["repo"]
    try:
        sync_stack(
            repo_path=repo,
            remote=remote,
            default_base=default_base,
            marker_key=marker,
            dry_run=dry_run,
        )
    except subprocess.CalledProcessError as err:
        click.echo(err.stderr or str(err), err=True)
        sys.exit(err.returncode or 1)
    except Exception as exc:
        click.echo(str(exc), err=True)
        sys.exit(1)


if __name__ == "__main__":
    print("main")
    main()
