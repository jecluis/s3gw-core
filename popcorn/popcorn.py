#!/usr/bin/env python3
#
# Copyright 2022 SUSE, LLC.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Dict, List, Optional
import click
import pydantic
import yaml
from libpopcorn import git, podman


class ConfigNotFoundError(Exception):
    pass


def get_config_default_path() -> Path:
    homedir = os.getenv("HOME", None)
    if homedir is None:
        return Path("./popcorn.yml")
    path = Path(homedir).joinpath(".config", "popcorn")
    path.mkdir(parents=True, exist_ok=True)
    return path.joinpath("config.yml")


class AdditionalRepositoryConfig(pydantic.BaseModel):
    name: str
    url: str


class Config(pydantic.BaseModel):
    workspace: Path
    ccache: Optional[Path]
    additional_repos: Optional[List[AdditionalRepositoryConfig]]


class Context:
    config_file: Path
    config: Optional[Config]

    def __init__(self):
        self.config_file = get_config_default_path()
        self.config = None

    def parse_config(self) -> Config:
        if not self.config_file.exists():
            raise ConfigNotFoundError()

        with self.config_file.open("r") as fd:
            try:
                raw = yaml.safe_load(fd)
            except yaml.YAMLError as e:
                click.echo(
                    f"error parsing config file at '{self.config_file}': {e}"
                )
                sys.exit(1)

        try:
            self.config = pydantic.parse_obj_as(Config, raw)
        except pydantic.ValidationError:
            click.echo("error validating config")
            sys.exit(1)

        return self.config


def get_ctx(ctx: click.Context) -> Context:
    return ctx.obj


@click.group()
@click.option(
    "--config",
    "-c",
    "configfile",
    type=click.Path(file_okay=True, dir_okay=False),
    help="Specify config file.",
)
@click.pass_context
def cli(cctx: click.Context, configfile: str):
    ctx = Context()

    if configfile:
        ctx.config_file = Path(configfile)
        try:
            ctx.parse_config()
        except ConfigNotFoundError:
            pass
    cctx.obj = ctx


@cli.command(help="Prepare environment.")
@click.argument("path", type=click.Path(file_okay=False))
@click.option(
    "--ccache", type=click.Path(file_okay=False), help="CCache directory."
)
@click.option(
    "--ccache-size", type=click.IntRange(1), help="CCache size, in GB."
)
@click.pass_context
def prepare(
    cctx: click.Context,
    path: str,
    ccache: Optional[str],
    ccache_size: Optional[int],
):

    print(f"ccache: {ccache}, size: {ccache_size}")

    wspath = Path(path)
    wspath.mkdir(parents=True, exist_ok=True)

    ccache_path = None
    ccache_env: Optional[Dict[str, str]] = None
    if ccache is not None:
        ccache_path = Path(ccache)
        ccache_path.mkdir(parents=True, exist_ok=True)
        ccache_env = {"CCACHE_DIR": ccache}

    if ccache_size is not None:
        p = subprocess.run(
            ["ccache", "-M", f"{ccache_size}G"],
            env=ccache_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if p.returncode != 0:
            click.echo(f"error setting ccache size: {p.stderr}")

    def clone_repo(name: str) -> bool:
        repo_path = wspath.joinpath(f"{name}.git")
        if repo_path.exists():
            return True
        cmd = [
            "git",
            "clone",
            f"https://github.com/aquarist-labs/{name}",
            repo_path.as_posix(),
        ]
        click.echo(f"cloning '{name}'...")
        p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if p.returncode != 0:
            click.echo(f"error cloning '{name}': {p.stderr}")
            return False
        return True

    for r in ["ceph", "s3gw-tools"]:
        if not clone_repo(r):
            click.echo(f"unable to clone repository '{r}'")
            sys.exit(1)

    # write config
    config = Config(workspace=wspath, ccache=ccache_path, additional_repos=None)

    ctx = get_ctx(cctx)
    assert ctx.config_file is not None
    ctx.config_file.parent.mkdir(parents=True, exist_ok=True)

    with ctx.config_file.open("w") as fd:
        d = json.loads(config.json())
        yaml.dump(d, fd)
    click.echo(f"wrote config to {ctx.config_file}")


@cli.command("show-config")
@click.pass_context
def show_config(cctx: click.Context):
    ctx = get_ctx(cctx)
    if ctx.config is None:
        click.echo("config not found.")
        sys.exit(0)

    click.echo(f"workspace: {ctx.config.workspace}")
    click.echo(f"   ccache: {ctx.config.ccache}")
    if ctx.config.additional_repos is not None:
        click.echo("+   repos")
        if len(ctx.config.additional_repos) == 0:
            click.echo("`- none")
        else:
            for repo in ctx.config.additional_repos:
                click.echo(f"`- name: {repo.name}")
                click.echo(f"    url: {repo.url}")


@cli.group("run", help="Run workflow on a branch/PR.")
def workflow_run():
    pass


@workflow_run.command("branch", help="Run workflow on a branch.")
@click.argument("name", type=str)
@click.option(
    "--remote",
    "-r",
    type=str,
    help="Obtain from specified remote repository.",
)
@click.option("--tools-pr-id", "-t", type=int, help="Tools PR ID.")
@click.pass_context
def workflow_run_branch(
    cctx: click.Context,
    name: str,
    remote: Optional[str],
    tools_pr_id: Optional[int],
):
    ctx = get_ctx(cctx)
    assert ctx.config is not None
    wspath = ctx.config.workspace

    origin = "origin" if remote is None else remote

    remotes = git.get_remotes(wspath, "ceph")
    if origin not in remotes:
        # try adding remote from config
        if ctx.config.additional_repos is None:
            click.echo(f"unable to find remote '{remote}.'")
            sys.exit(1)
        found = False
        for r in ctx.config.additional_repos:
            if r.name == origin:
                found = True
                git.add_remote(wspath, "ceph", r.name, r.url)
                break
        if not found:
            click.echo(f"unable to find remote '{remote}' in config.")

        # update remotes list
        remotes = git.get_remotes(wspath, "ceph")

    assert origin in remotes

    git.update(wspath, "ceph", origin)

    # fetch branch
    try:
        ceph_branch = git.fetch(wspath, "ceph", origin, name)
    except git.PopcornError as e:
        click.echo(e.message)
        sys.exit(1)

    run_for_local(wspath, ctx.config, ceph_branch, tools_pr_id)


def run_tests(path: Path, s3gw_img: str) -> bool:
    click.echo("running tests...")

    tests_dir = path.joinpath("tests").absolute()
    tests_path = tests_dir.joinpath("run-tests.sh")
    print(tests_path)
    assert tests_path.exists()
    assert tests_path.is_file()

    click.echo(f"starting s3gw image {s3gw_img}...")
    cmd = [
        "podman",
        "run",
        "--detach",
        "--replace",
        "--name",
        "popcorn-s3gw",
        "-p",
        "7480:7480",
        s3gw_img,
    ]
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if p.returncode != 0:
        click.echo(f"error starting s3gw container: {p.stderr}")
        sys.exit(1)

    container_id = p.stdout.decode("utf-8").strip()
    assert len(container_id) > 0

    success = False
    click.echo("run tests against s3gw running on port 7480...")
    cmd = ["./run-tests.sh"]
    p = subprocess.run(cmd, cwd=tests_dir)
    if p.returncode != 0:
        click.echo("error running tests!!")
    else:
        click.echo("tests run successfully :)")
        success = True

    click.echo("stop s3gw container...")
    cmd = ["podman", "stop", "popcorn-s3gw"]
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if p.returncode != 0:
        click.echo(f"error stopping container {container_id}: {p.stderr}")
        sys.exit(1)

    return success


def check_pr_exists(repo: str, id: int, not_merged: bool) -> None:
    click.echo(f"check if PR  #{id} exists on repository '{repo}'...")
    try:
        res = git.pr_exists(repo, id)
        if not_merged and "merged" in res and res["merged"]:
            click.echo(f"PR #{id} has been merged.")
            sys.exit(1)
    except git.NotFoundError:
        click.echo(f"error: PR #{id} not found on repository '{repo}'.")
        sys.exit(1)
    except git.PopcornError:
        click.echo(f"error obtaining PR #{id} on repository '{repo}'.")
        sys.exit(1)


@workflow_run.command("pr", help="Run workflow on a PR.")
@click.argument("PR-ID", type=int)
@click.option("--tools-pr-id", "-t", help="Tools PR ID.", type=int)
@click.pass_context
def workflow_run_pr(
    cctx: click.Context, pr_id: int, tools_pr_id: Optional[int]
):
    check_pr_exists("ceph", pr_id, True)

    ctx = get_ctx(cctx)
    assert ctx.config is not None
    base_path = ctx.config.workspace

    try:
        click.echo(f"fetch PR #{pr_id} for ceph...")
        ceph_branch = git.pr_fetch("ceph", pr_id, base_path)
    except git.PopcornError as e:
        click.echo(e.message)
        sys.exit(1)

    run_for_local(base_path, ctx.config, ceph_branch, tools_pr_id)


def run_for_local(
    wspath: Path,
    config: Config,
    ceph_branch: str,
    tools_pr_id: Optional[int],
) -> None:

    tools_branch = "main"
    fetch_tools_pr = False
    if tools_pr_id is not None:
        check_pr_exists("s3gw-tools", tools_pr_id, False)
        fetch_tools_pr = True
    if fetch_tools_pr:
        assert tools_pr_id is not None
        click.echo(f"fetch PR #{tools_pr_id} for s3gw-tools...")
        try:
            tools_branch = git.pr_fetch("s3gw-tools", tools_pr_id, wspath)
        except git.PopcornError as e:
            click.echo(e.message)
            sys.exit(1)

    # start with a clean repo
    try:
        click.echo("cleanup repository 'ceph'...")
        git.clean_repo(wspath, "ceph")
        click.echo("cleanup repository s3gw-tools...")
        git.clean_repo(wspath, "s3gw-tools")
    except git.PopcornError as e:
        click.echo(e.message)
        sys.exit(1)

    # change to PR branch
    try:
        click.echo(f"checkout branch {ceph_branch} on repository 'ceph'...")
        git.checkout_branch(wspath, "ceph", ceph_branch)
        click.echo(
            f"checkout branch {tools_branch} on repository 's3gw-tools'..."
        )
        git.checkout_branch(wspath, "s3gw-tools", tools_branch)
    except git.PopcornError as e:
        click.echo(e.message)
        sys.exit(1)

    # check if we already have an s3gw image built for this branch/commit
    try:
        images = podman.list_images()
    except podman.PopcornError as e:
        click.echo(e.message)
        sys.exit(1)

    try:
        ceph_commit = git.get_latest_commit(wspath, "ceph")
    except git.PopcornError as e:
        click.echo(e.message)
        sys.exit(1)
    assert len(ceph_commit) > 0

    click.echo(f"check for existing s3gw image for commit {ceph_commit}...")
    s3gw_image_name = "popcorn/s3gw"
    s3gw_image: Optional[str] = None
    for img in images:
        if img.name.endswith(s3gw_image_name) and ceph_commit == img.tag:
            s3gw_image = f"{img.name}:{img.tag}"
            break

    if s3gw_image is not None:
        click.echo(f"found image {s3gw_image}, skip building.")
        run_tests(wspath.joinpath("s3gw-tools.git"), s3gw_image)
        return
    s3gw_image = f"{s3gw_image_name}:{ceph_commit}"

    click.echo(f"must build an s3gw image for branch {ceph_branch}...")

    # create build container from tools repository
    try:
        tools_commit = git.get_latest_commit(wspath, "s3gw-tools")
    except git.PopcornError as e:
        click.echo(e.message)
        sys.exit(1)
    assert len(tools_commit) > 0

    build_img_name = "popcorn/builder"
    build_image: Optional[str] = None
    for img in images:
        if build_img_name in img.name and tools_commit == img.tag:
            build_image = f"{img.name}:{img.tag}"
            break
    if build_image is not None:
        click.echo(f"found builder image {build_image}")
    else:
        click.echo(f"creating new builder image...")
        try:
            build_image = podman.build_image(
                wspath.joinpath("s3gw-tools.git"), tools_commit
            )
        except podman.PopcornError as e:
            click.echo(e.message)
            sys.exit(1)

    # build ceph repository
    click.echo(f"building branch {ceph_branch}...")
    cephpath = wspath.joinpath("ceph.git").absolute().as_posix()

    vols: List[str] = []
    env: List[str] = []

    if config.ccache is not None:
        ccache_path = Path(config.ccache).absolute().as_posix()
        vols.append(f"{ccache_path}:/srv/ccache")
        env.append("S3GW_CCACHE_DIR=/srv/ccache")

    vols.append(f"{cephpath}:/srv/ceph")

    try:
        podman.run(
            build_image,
            "popcorn-builder",
            volumes=vols,
            env=env,
            replace=True,
        )
    except podman.PopcornError as e:
        click.echo(e.message)
        sys.exit(1)

    # create s3gw image from sources
    click.echo(f"building s3gw image for branch at {s3gw_image}")
    dockerfile = wspath.joinpath(
        "s3gw-tools.git", "build", "Dockerfile.build-container"
    ).absolute()
    assert dockerfile.exists()
    cmd = [
        "podman",
        "build",
        "-t",
        s3gw_image,
        "-f",
        dockerfile.as_posix(),
        wspath.joinpath("ceph.git", "build"),
    ]
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if p.returncode != 0:
        click.echo(f"error building image {s3gw_image}: {p.stderr}")
        sys.exit(1)

    run_tests(wspath.joinpath("s3gw-tools.git"), s3gw_image)


if __name__ == "__main__":
    cli()
