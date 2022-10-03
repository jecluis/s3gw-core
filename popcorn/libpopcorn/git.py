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

from pathlib import Path
import subprocess
from typing import Any, Dict, Optional
import requests

from .common import PopcornError, NotFoundError


def pr_exists(repo: str, pr_id: int) -> Dict[str, Any]:
    """
    Check whether a given PR exists for the provided repository. If not, raises
    error; otherwise, return response map.
    """
    # check pr exists
    req = requests.get(
        f"https://api.github.com/repos/aquarist-labs/{repo}/pulls/{pr_id}",
        headers={"Accept": "application/vnd.github+json"},
    )
    if req.status_code == 404:
        raise NotFoundError()
    elif req.status_code != 200:
        raise PopcornError()
    return req.json()


def fetch(wspath: Path, repo: str, origin: str, branch: str) -> str:
    """
    Fetch a branch from the specified origin.
    """
    repopath = wspath.joinpath(f"{repo}.git")
    assert repopath.exists()
    assert repopath.joinpath(".git").exists()

    localname = f"popcorn/{origin}/{branch}"

    cmd = ["git", "branch", "--show-current"]
    p = subprocess.run(
        cmd, cwd=repopath, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    if p.returncode != 0:
        raise PopcornError(f"error obtaining branch: {p.stderr}")

    cur_branch = p.stdout.decode("utf-8").strip()

    cmd = [
        "git",
        "fetch",
        origin,
        f"{branch}:{localname}",
    ]
    if cur_branch == localname:
        cmd = [
            "git",
            "pull",
            "--force",
            origin,
            f"{branch}:{localname}",
        ]

    p = subprocess.run(
        cmd,
        cwd=repopath,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if p.returncode != 0:
        raise PopcornError(
            f"error fetching branch {branch} for repository {repo}: {p.stderr}"
        )
    return localname


def pr_fetch(repo: str, pr_id: int, wspath: Path) -> str:
    """
    Fetch a PR branch from a given repository.
    """
    return fetch(wspath, repo, "origin", f"pull/{pr_id}/head")


def clean_repo(path: Path, repo: str) -> None:
    """
    Clean up the provided repository, including submodules.
    """
    repopath = path.joinpath(f"{repo}.git")
    assert repopath.exists()
    assert repopath.joinpath(".git").exists()

    cmd = [
        "git",
        "submodule",
        "foreach",
        "git clean -fdx",
    ]
    p = subprocess.run(
        cmd,
        cwd=repopath,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if p.returncode != 0:
        raise PopcornError(
            f"error clearing repository {repo}'s submodules: {p.stderr}"
        )

    cmd = ["git", "clean", "-fdx"]
    p = subprocess.run(
        cmd,
        cwd=repopath,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if p.returncode != 0:
        raise PopcornError(f"error clearing repository {repo}: {p.stderr}")


def checkout_branch(path: Path, repo: str, branch: str) -> None:
    """
    Checkout a branch on a given repository.
    """
    repopath = path.joinpath(f"{repo}.git")
    assert repopath.exists()
    assert repopath.joinpath(".git").exists()

    cmd = ["git", "checkout", branch]
    p = subprocess.run(
        cmd,
        cwd=repopath.as_posix(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if p.returncode != 0:
        raise PopcornError(f"error checking out branch {branch}: {p.stderr}")


def get_latest_commit(path: Path, repo: str) -> str:
    """
    Obtain latest commit SHA from specified repository's checked out branch.
    """
    repopath = path.joinpath(f"{repo}.git")
    assert repopath.exists()
    assert repopath.joinpath(".git").exists()
    cmd = ["git", "rev-parse", "--short", "HEAD"]
    p = subprocess.run(
        cmd, cwd=repopath, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    if p.returncode != 0:
        raise PopcornError(
            f"error obtaining latest commit on repository {repo}: {p.stderr}"
        )
    return p.stdout.decode("utf-8").strip()


def get_remotes(path: Path, repo: str) -> Dict[str, str]:
    """
    Obtain repository's remotes.
    """
    repopath = path.joinpath(f"{repo}.git")
    assert repopath.exists()
    assert repopath.joinpath(".git").exists()
    cmd = ["git", "remote", "-v"]
    p = subprocess.run(
        cmd, cwd=repopath, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    if p.returncode != 0:
        raise PopcornError(
            f"error obtaining repository '{repo}' remotes: {p.stderr}"
        )
    outlst = p.stdout.decode("utf-8").splitlines()
    remotes: Dict[str, str] = {}
    for remote in outlst:
        name, url, rtype = remote.split()
        if "fetch" not in rtype:
            continue
        remotes[name] = url
    return remotes


def add_remote(path: Path, repo: str, name: str, remote: str) -> None:
    """
    Add a given name with provided url as a remote to a repository.
    """
    repopath = path.joinpath(f"{repo}.git")
    assert repopath.exists()
    assert repopath.joinpath(".git").exists()
    cmd = ["git", "remote", "add", name, f"{remote}/{repo}"]
    p = subprocess.run(
        cmd, cwd=repopath, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    if p.returncode != 0:
        raise PopcornError(
            f"error adding remote '{name}' to repository '{repo}': {p.stderr}"
        )


def update(path: Path, repo: str, remote: Optional[str] = None) -> None:
    """
    Update references, either for all remotes or for a provided remote.
    """
    repopath = path.joinpath(f"{repo}.git")
    assert repopath.exists()
    assert repopath.joinpath(".git").exists()
    cmd = ["git", "remote", "update"]
    if remote is not None:
        cmd.append(remote)
    p = subprocess.run(
        cmd, cwd=repopath, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    if p.returncode != 0:
        print(p.stdout)
        raise PopcornError(f"error updating remotes: {p.stderr}")
