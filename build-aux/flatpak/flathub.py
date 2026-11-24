#!/usr/bin/env python3
"""Write the manifest for Flathub from the development manifest.

    python3 build-aux/flatpak/flathub.py v1.1.2 [--commit SHA] [--out DIR]

The manifest next to this script builds the local checkout (a `dir`
source), which Flathub does not accept. This writes the same manifest with
Tavletta's source replaced by the release tag and its commit, plus the
local files the manifest refers to (patches, licence files), into DIR
(default: build/flathub): the files of the Flathub pull request.

Flathub fetches the tag from the GitHub mirror (REPO_URL): the main
repository's server limits automated clients (its CrowdSec takes frequent
fetches for a crawler), while Flathub's builders and its update checker
fetch from GitHub without such limits. Every tag reaches the mirror, and
the commit pins the content, whichever server serves it.

The commit is the tag's in this repository unless --commit is given; either
way the tag must be on the mirror at that very commit (checked with git
ls-remote), and a local tag must agree. Any release that contains this
manifest's build can be submitted: v1.1.2 and later (v1.1.0 had no
CREDITS.md yet).
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess  # nosec B404 - runs git rev-parse and ls-remote on a checked tag name
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
APP_ID = "xyz.joshuahirsig.Tavletta"
MANIFEST = HERE / f"{APP_ID}.yml"
REPO_URL = "https://github.com/joshii-h/tavletta.git"
# the line that starts the development source, the last source of the manifest
DIR_MARKER = "      # local checkout, for development builds"
# semver, as the release workflows take it (.forgejo/workflows/release.yml):
# no hyphen inside the pre-release, which the packages could not map
TAG = re.compile(r"v(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(-[0-9A-Za-z]+(\.[0-9A-Za-z]+)*)?")
COMMIT = re.compile(r"[0-9a-f]{40}")

GIT_SOURCE = """\
      # the release, by tag and its commit
      - type: git
        url: {url}
        tag: {tag}
        commit: '{commit}'  # quoted: a SHA of digits only would be a number
        x-checker-data:
          type: git
          tag-pattern: ^v([\\d.]+)$
"""


def flathub_manifest(text: str, tag: str, commit: str) -> str:
    """The manifest text with the `dir` source of Tavletta replaced by `tag`."""
    if not TAG.fullmatch(tag):
        raise ValueError(f"not a release tag: {tag}")
    if not COMMIT.fullmatch(commit):
        raise ValueError(f"not a full commit SHA: {commit}")
    if text.count(DIR_MARKER) != 1:
        raise ValueError("the development source is not where expected")
    head, _, tail = text.partition(DIR_MARKER)
    tail = tail.partition("\n")[2]  # the rest of the marker's line
    # its comment, then the dir source and its fields (indented deeper), nothing else
    if not re.fullmatch(r"(?: {6}#.*\n)* {6}- type: dir\n(?: {8,}\S.*\n|\n)*", tail):
        raise ValueError("something follows the development source; it must be last")
    return head + GIT_SOURCE.format(url=REPO_URL, tag=tag, commit=commit)


def local_files(text: str) -> list[str]:
    """Files the manifest takes from next to it (`path:` of file and patch sources)."""
    return sorted(set(re.findall(r"^\s+path: ([\w.-]+)$", text, re.M)))


def _git(args: list[str], run=subprocess.run) -> str | None:
    """git's output, None if it fails (no such tag)."""
    done = run(  # nosec B603 B607 - git, with a checked tag name
        ["git", *args], cwd=HERE, capture_output=True, text=True, check=False
    )
    return done.stdout if done.returncode == 0 else None


def local_commit(tag: str, run=subprocess.run) -> str | None:
    """The commit of the tag in this repository, None without that tag."""
    out = _git(["rev-parse", "--verify", "--quiet", f"refs/tags/{tag}^{{commit}}"], run)
    return (out or "").strip() or None


def remote_commit(tag: str, run=subprocess.run) -> str | None:
    """The commit of the tag on the mirror Flathub fetches from, None
    without that tag there (an annotated tag: the commit it points to)."""
    out = _git(["ls-remote", "--tags", REPO_URL, f"refs/tags/{tag}", f"refs/tags/{tag}^{{}}"], run)
    if out is None:
        raise ValueError(f"cannot reach {REPO_URL}")
    refs = {ref: sha for sha, _, ref in (line.partition("\t") for line in out.splitlines())}
    return refs.get(f"refs/tags/{tag}^{{}}") or refs.get(f"refs/tags/{tag}")


def commit_of(tag: str, given: str | None = None, run=subprocess.run) -> str:
    """The commit to pin: `given` or the local tag's, the same as the
    mirror's. ValueError naming what does not agree."""
    local = local_commit(tag, run)
    commit = given or local
    if commit is None:
        raise ValueError(f"no tag {tag} here: give its commit with --commit")
    if not COMMIT.fullmatch(commit):
        raise ValueError(f"not a full commit SHA: {commit}")
    if local is not None and local != commit:
        raise ValueError(f"--commit {commit} is not the tag {tag} here ({local})")
    remote = remote_commit(tag, run)
    if remote is None:
        raise ValueError(f"tag {tag} is not on {REPO_URL} (push it, wait for the mirror)")
    if remote != commit:
        raise ValueError(f"tag {tag} is {remote} on {REPO_URL}, not {commit}")
    return commit


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("tag", help="the release tag, e.g. v1.1.2")
    parser.add_argument("--commit", help="the tag's commit (default: the tag's here)")
    parser.add_argument("--out", type=Path, default=HERE.parent.parent / "build" / "flathub")
    args = parser.parse_args(argv)
    if not TAG.fullmatch(args.tag):
        parser.error(f"not a release tag: {args.tag}")
    try:
        commit = commit_of(args.tag, args.commit)
    except ValueError as e:
        parser.error(str(e))
    text = MANIFEST.read_text(encoding="utf-8")
    manifest = flathub_manifest(text, args.tag, commit)
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / MANIFEST.name).write_text(manifest, encoding="utf-8")
    for name in local_files(manifest):
        shutil.copyfile(HERE / name, args.out / name)
    print(f"{args.out}: {MANIFEST.name} ({args.tag}, {commit}) and", *local_files(manifest))
    return 0


if __name__ == "__main__":
    sys.exit(main())
