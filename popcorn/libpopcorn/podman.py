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
from pathlib import Path
import subprocess
from typing import List, Optional
import pydantic

from .common import PopcornError


class PodmanImage(pydantic.BaseModel):
    name: str
    tag: str
    sha: str


def list_images() -> List[PodmanImage]:
    """
    Obtain a list of images from podman.
    """
    cmd = ["podman", "images", "--format", "json"]
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if p.returncode != 0:
        raise PopcornError(f"error obtaining podman images: {p.stderr}")

    images_lst: List[PodmanImage] = []
    res = json.loads(p.stdout.decode("utf-8"))
    for entry in res:
        assert "Id" in entry
        sha = entry["Id"]
        if not "Names" in entry:
            # unnamed images are typically dangling references
            continue
        names: List[str] = entry["Names"]
        for name in names:
            img_name, img_tag = name.split(":")
            images_lst.append(PodmanImage(name=img_name, tag=img_tag, sha=sha))

    return images_lst


def build_image(repopath: Path, tag: str) -> str:
    """
    Create a builder image with specified tag.
    """
    path = repopath.joinpath("build")
    assert path.exists()

    imgname = f"popcorn/builder:{tag}"
    cmd = [
        "podman",
        "build",
        "-t",
        imgname,
        "-f",
        "Dockerfile.build-radosgw",
        path,
    ]
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if p.returncode != 0:
        raise PopcornError(
            f"error creating builder container {imgname}: {p.stderr}"
        )
    return imgname


def run(
    img: str,
    name: str,
    *,
    volumes: List[str] = [],
    env: List[str] = [],
    ports: List[str] = [],
    replace: bool = False,
    detach: bool = False,
    capture_output: bool = True,
) -> Optional[str]:
    cmd = [
        "podman",
        "run",
        "--name",
        name,
    ]
    if replace:
        cmd.append("--replace")
    if detach:
        cmd.append("--detach")

    for vol in volumes:
        orig, dest = vol.split(":")
        assert len(orig) > 0 and len(dest) > 0
        cmd.extend(["-v", vol])

    for var in env:
        n, v = var.split("=")
        assert len(n) > 0 and len(v) > 0
        cmd.extend(["-e", var])

    for port in ports:
        orig, dest = port.split(":")
        assert len(orig) > 0 and len(dest) > 0
        cmd.extend(["-p", port])

    cmd.append(img)

    out = subprocess.PIPE if capture_output else None
    err = subprocess.PIPE if capture_output else None
    p = subprocess.run(cmd, stdout=out, stderr=err)
    if p.returncode != 0:
        raise PopcornError(f"error running container: {p.stderr}")

    if detach:
        res = p.stdout.decode("utf-8").split()
        assert len(res) > 0
        return res[len(res) - 1]

    return None
