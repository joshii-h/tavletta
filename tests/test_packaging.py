"""What the package and install.sh ship: the licence files are there."""

from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def test_license_files_exist_and_install_sh_copies_them():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    install = (ROOT / "install.sh").read_text(encoding="utf-8")
    for name in project["license-files"]:
        assert (ROOT / name).is_file(), name
        top = name.split("/")[0]
        assert '"$SRC/%s"' % top in install, name  # the build copy has it


def test_the_sdist_carries_what_the_notices_link_to():
    manifest = (ROOT / "MANIFEST.in").read_text(encoding="utf-8")
    notices = (ROOT / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
    for link in re.findall(r"\]\(((?:build-aux|tavletta)/[^)]+)\)", notices):
        assert (ROOT / link).is_file(), link
        assert link.startswith("tavletta/") or link in manifest, link


def test_check_sh_runs_what_the_ci_runs():
    """tools/check.sh is the CI locally: the same commands, the same flags."""
    check = (ROOT / "tools" / "check.sh").read_text(encoding="utf-8")
    ci = (ROOT / ".forgejo" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    for command in (
        "ruff format --check .",
        "ruff check --output-format=full .",
        "pytest -q --cov=tavletta --cov-report=term-missing",
        "mypy",
        "bandit -c pyproject.toml -r tavletta -ll",
        "radon cc -s -n C tavletta",
        "xenon --max-absolute C --max-modules A --max-average A tavletta",
        "pip-audit --progress-spinner off .",
        "msgfmt --check",
        "appstreamcli validate --no-net --explain data/*.metainfo.xml",
        "desktop-file-validate data/*.desktop",
    ):
        assert command in check and command in ci, command
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    dev = " ".join(project["dependency-groups"]["dev"])
    # pytest-xdist: locally one worker per CPU unless PYTEST_WORKERS says
    # otherwise, in the CI a fixed few
    assert '"-n ${PYTEST_WORKERS:-auto}"' in check
    assert "--cov-report=term-missing -n 4\n" in ci
    for tool in (
        "pytest",
        "pytest-xdist",
        "ruff",
        "mypy",
        "bandit",
        "radon",
        "xenon",
        "pip-audit",
        "pyyaml",
    ):
        assert tool in dev, tool
    for loop in ("for po in po/*.po site/po/*.po; do", "for po in site/po/*.po; do"):
        assert loop in check and loop in ci, loop  # the documentation's catalogs too
    assert ci.count("pip install -q -e . --group dev") == 2  # both jobs: the same tools
    assert ci.count('pip install -q --upgrade "pip>=25.1"') == 2  # --group needs it
    contributing = (ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")
    assert "-e . --group dev" in contributing  # the development setup, as the CI installs


def test_the_flatpak_ships_every_licence_once():
    """One layout: share/licenses/<app id>/<module>/, where flatpak-builder
    copies a module's own LICENSE*/COPYING* by itself; installed by hand
    only what it does not find (licences inside wheels, Tavletta's notices
    and data licences), into the same folder, never a second copy at the
    top. What the app does not use is cleaned up; every downloaded source
    can be checked for updates."""
    import re

    import yaml  # in the dev dependency group: the test runs, never skips

    path = ROOT / "build-aux" / "flatpak" / "xyz.joshuahirsig.Tavletta.yml"
    manifest = yaml.safe_load(path.read_text(encoding="utf-8"))
    target = "${FLATPAK_DEST}/share/licenses/${FLATPAK_ID}/"
    by_hand: dict[str, str] = {}
    for module in manifest["modules"]:
        commands = module.get("build-commands", []) + module.get("post-install", [])
        for command in commands:
            for folder in re.findall(re.escape(target) + r"([^\s/]*)", command):
                assert folder == module["name"], command  # the module's own folder
                by_hand[module["name"]] = command
    assert set(by_hand) == {
        "python3-python-barcode",
        "poppler-data",
        "poppler",
        "tavletta",
    }
    assert "THIRD_PARTY_NOTICES.md" in by_hand["tavletta"]
    assert "CREDITS.md" in by_hand["tavletta"]  # the credits, no longer in a README it ships
    assert "LICENSE.glabels" in by_hand["tavletta"] and "LICENSE.mdi" in by_hand["tavletta"]
    assert "LICENSE.zint" not in "".join(by_hand.values())  # a source: copied by itself
    zint = next(m for m in manifest["modules"] if m["name"] == "python3-zint-bindings")
    assert {"type": "file", "path": "LICENSE.zint"} in zint["sources"]
    # Flathub builds from source: the sdist, patched for an offline CMake build
    # (its LICENSE.md at the top, which flatpak-builder copies by itself)
    assert [s["type"] for s in zint["sources"]] == ["archive", "patch", "file"]
    assert zint["sources"][0]["url"].endswith(".tar.gz")
    assert (path.parent / zint["sources"][1]["path"]).is_file()
    tavletta = manifest["modules"][-1]
    icons = next(c for c in tavletta["build-commands"] if "rsvg-convert" in c)
    assert icons.startswith("set -e; ")  # a failed size fails the build
    cleanup = [c for module in manifest["modules"] for c in module.get("cleanup", [])]
    assert "/share/man/man1/segno.1" in cleanup
    for unused in ("test", "cups_backend.py", "__main__.py", "print_text.py", "queue_*.py"):
        assert any(c.endswith("labelprinter/" + unused) for c in cleanup), unused
    for module in manifest["modules"]:
        for source in module.get("sources", []):
            if source.get("type") in ("file", "archive") and "url" in source:
                assert "x-checker-data" in source, (module["name"], source["url"])
            # prebuilt binaries only as pure Python wheels (py3-none-any), and
            # only where the sdist's build needs what the SDK lacks
            if source.get("url", "").endswith(".whl"):
                assert source["url"].endswith("-py3-none-any.whl"), source["url"]
                assert module["name"] in ("python3-pybind11", "python3-python-barcode"), module

    # the manifest for Flathub: the same, with the release tag instead of the checkout
    flathub = _flathub()
    text = path.read_text(encoding="utf-8")
    release = yaml.safe_load(flathub.flathub_manifest(text, "v1.1.2", "0" * 40))
    sources = [s for m in release["modules"] for s in m.get("sources", [])]
    assert not [s for s in sources if s["type"] == "dir"]  # Flathub rejects dir sources
    assert release["modules"][-1]["sources"] == [
        {
            "type": "git",
            "url": "https://github.com/joshii-h/tavletta.git",  # the mirror, see flathub.py
            "tag": "v1.1.2",
            "commit": "0" * 40,
            "x-checker-data": {"type": "git", "tag-pattern": r"^v([\d.]+)$"},
        }
    ]
    assert release["modules"][:-1] == manifest["modules"][:-1]
    for name in flathub.local_files(text):  # the files the pull request needs too
        assert (path.parent / name).is_file(), name
    assert set(flathub.local_files(text)) == {"LICENSE.zint", "zint-bindings-offline.patch"}


def _workflow(name: str, forge: str = ".github") -> dict:
    import yaml

    workflow = yaml.safe_load((ROOT / forge / "workflows" / name).read_text(encoding="utf-8"))
    workflow["on"] = workflow.pop(True, workflow.get("on"))  # YAML 1.1 reads `on` as true
    return workflow


def test_github_workflows_pin_actions_and_run_only_on_github():
    """Forgejo reads .github/workflows too: every job is guarded. Actions are
    pinned to a full commit SHA, with the version as a comment."""
    for path in sorted((ROOT / ".github" / "workflows").glob("*.yml")):
        text = path.read_text(encoding="utf-8")
        for line in re.findall(r"uses:\s*(\S.*)", text):
            assert re.fullmatch(r"[\w.-]+/[\w./-]+@[0-9a-f]{40}\s+# v\d+(\.\d+)*", line), (
                path.name,
                line,
            )
        for name, job in _workflow(path.name)["jobs"].items():
            assert job["if"].startswith("github.server_url == 'https://github.com'"), name


def test_the_container_workflow():
    workflow = _workflow("docker.yml")
    text = (ROOT / ".github" / "workflows" / "docker.yml").read_text(encoding="utf-8")
    on = workflow["on"]
    assert on["push"] == {"tags": ["v*"], "branches": ["main"]}
    assert on["pull_request"] == {"branches": ["main"]} and "workflow_dispatch" in on
    assert workflow["permissions"] == {"contents": "read"}
    assert workflow["env"]["IMAGE"] == "ghcr.io/joshii-h/tavletta"
    check, publish = workflow["jobs"]["check"], workflow["jobs"]["publish"]
    assert "permissions" not in check  # read-only, also for pull requests
    assert publish["needs"] == "check" and "github.event_name != 'pull_request'" in publish["if"]
    assert publish["permissions"] == {
        "contents": "read",
        "packages": "write",
        "id-token": "write",
        "attestations": "write",
    }

    def step(job: dict, uses: str = "", run: str = "") -> dict:
        found = [
            s
            for s in job["steps"]
            if (uses and s.get("uses", "").startswith(uses + "@"))
            or (run and run in s.get("run", ""))
        ]
        assert len(found) == 1, uses or run
        return found[0]

    smoke = step(check, run="packaging/docker/smoke-test.sh")
    assert smoke["env"]["EXPECT_VERSION"]  # a release reports its tag's version
    script = ROOT / "packaging" / "docker" / "smoke-test.sh"
    assert script.stat().st_mode & 0o111  # executable
    trivy = step(check, uses="aquasecurity/trivy-action")["with"]
    assert trivy["severity"] == "CRITICAL,HIGH" and trivy["ignore-unfixed"] is True
    assert trivy["exit-code"] == "${{ env.RELEASE == 'true' && '1' || '0' }}"
    assert re.fullmatch(r"v\d+\.\d+\.\d+", trivy["version"])  # not whatever is newest
    test_build = step(check, uses="docker/build-push-action")["with"]
    assert test_build["platforms"] == "linux/amd64" and test_build["load"] is True
    # a release has today's security updates, scanned as they are pushed
    assert test_build["no-cache"] == "${{ env.RELEASE == 'true' }}"

    build = step(publish, uses="docker/build-push-action")["with"]
    assert build["file"] == "packaging/docker/Dockerfile"
    assert build["platforms"] == "linux/amd64,linux/arm64"
    assert build["push"] == "${{ env.PUSH == 'true' }}"
    assert publish["env"]["PUSH"] == "${{ github.event_name == 'push' }}"
    assert build["sbom"] is True and build["provenance"] == "mode=max"
    assert build["cache-from"].splitlines() == [
        "type=gha,scope=check",
        "${{ env.RELEASE != 'true' && 'type=gha,scope=publish' || '' }}",
    ]
    assert build["cache-to"] == "type=gha,scope=publish,mode=max"
    assert step(publish, uses="docker/setup-qemu-action")["with"]["platforms"] == "arm64"
    attest = step(publish, uses="actions/attest-build-provenance")
    assert attest["with"]["push-to-registry"] is True
    assert attest["with"]["subject-digest"] == "${{ steps.build.outputs.digest }}"
    assert "cosign sign --yes" in step(publish, run="cosign sign")["run"]
    login = step(publish, uses="docker/login-action")
    assert login["with"]["password"] == "${{ github.token }}"  # no long-lived token
    for writing in (login, attest, step(publish, run="cosign sign")):
        assert writing["if"] == "env.PUSH == 'true'"

    meta = step(publish, uses="docker/metadata-action")["with"]
    for tag in (
        "type=semver,pattern={{version}}",
        "type=semver,pattern={{major}}.{{minor}}",
        "type=edge,branch=main",
    ):
        assert tag in meta["tags"], tag
    assert "type=raw,value=latest," in meta["tags"] and meta["flavor"] == "latest=false"
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    for field in ("labels", "annotations"):
        assert "org.opencontainers.image.licenses=%s\n" % project["license"] in meta[field]
        assert (
            "org.opencontainers.image.source=https://github.com/joshii-h/tavletta\n" in meta[field]
        )
        assert (
            "org.opencontainers.image.url=https://git.joshuahirsig.xyz/joshii/tavletta\n"
            in (meta[field])
        )
    assert "secrets." not in text  # GITHUB_TOKEN only


def test_the_container_image_and_compose_files():
    import yaml

    from tavletta import __version__

    docker = ROOT / "packaging" / "docker"
    dockerfile = (docker / "Dockerfile").read_text(encoding="utf-8")
    assert re.search(r"^USER tavletta$", dockerfile, re.M)
    assert "HEALTHCHECK" in dockerfile and "/api/v1/health" in dockerfile
    copy = r"^COPY .*THIRD_PARTY_NOTICES\.md CREDITS\.md \./$"
    assert re.search(copy, dockerfile, re.M)  # setuptools: license-files
    assert "/src/THIRD_PARTY_NOTICES.md /src/CREDITS.md /usr/share/doc/tavletta/" in dockerfile
    assert "gir1.2-poppler-0.18" in dockerfile  # PDF pages of shipping labels
    assert "apt-get upgrade -y" in dockerfile  # fixes newer than the base image
    compose = yaml.safe_load((docker / "compose.yaml").read_text(encoding="utf-8"))
    service = compose["services"]["tavletta"]
    assert "build" not in service
    minor = ".".join(__version__.split(".")[:2])
    assert service["image"] == "ghcr.io/joshii-h/tavletta:" + minor  # bumped with the version
    for doc in ("README.md", "docs/install.md"):  # the install instructions too
        assert service["image"] in (ROOT / doc).read_text(encoding="utf-8"), doc
    build = yaml.safe_load((docker / "compose.build.yaml").read_text(encoding="utf-8"))
    built = build["services"]["tavletta"]
    assert (docker / built["build"]["context"] / built["build"]["dockerfile"]).is_file()
    assert built["pull_policy"] == "build"


def _markdown_anchors(text: str) -> set[str]:
    """The anchors of a Markdown file's headings: GitHub's slugs and
    explicit `{ #id }` attributes."""
    anchors = set()
    for heading in re.findall(r"^#+ (.+)$", re.sub(r"```.*?```", "", text, flags=re.S), re.M):
        explicit = re.search(r"\{ #([\w-]+) \}$", heading)
        if explicit:
            anchors.add(explicit.group(1))
            heading = heading[: explicit.start()].strip()
        slug = re.sub(r"[^\w\- ]", "", heading.lower()).replace(" ", "-")
        anchors.add(slug)
    return anchors


def test_the_readme_is_short_and_every_markdown_link_resolves():
    """The README is a landing page; the details live in docs/, CONTRIBUTING.md
    and CREDITS.md. Every relative link in these files points at a file,
    and at a heading of it, that are there; docs/README.md lists every document."""
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert len(readme.splitlines()) <= 100
    for doc in ("docs/README.md", "docs/features.md", "docs/install.md", "docs/cli.md"):
        assert "](%s)" % doc in readme, doc
    for doc in ("CONTRIBUTING.md", "CODE_OF_CONDUCT.md", "SECURITY.md", "ACCESSIBILITY.md"):
        assert "](%s)" % doc in readme, doc
    assert "](CREDITS.md)" in readme and "](THIRD_PARTY_NOTICES.md)" in readme
    files = [
        ROOT / name
        for name in (
            "README.md",
            "CONTRIBUTING.md",
            "CREDITS.md",
            "THIRD_PARTY_NOTICES.md",
            "ACCESSIBILITY.md",
            "SECURITY.md",
            "CODE_OF_CONDUCT.md",
        )
    ]
    files += sorted((ROOT / "docs").glob("*.md"))
    index = (ROOT / "docs" / "README.md").read_text(encoding="utf-8")
    for doc in sorted((ROOT / "docs").glob("*.md")) + [ROOT / "docs" / "openapi.yaml"]:
        assert doc.name == "README.md" or "](%s)" % doc.name in index, doc.name
    for path in files:
        text = re.sub(r"```.*?```", "", path.read_text(encoding="utf-8"), flags=re.S)
        links = re.findall(r"\]\(([^)\s]+)\)", text) + re.findall(
            r'(?:src|srcset)="([^"]+)"', text
        )
        for link in links:
            if re.match(r"[a-z]+:", link):
                continue  # https:, mailto:
            target, _, anchor = link.partition("#")
            dest = (path.parent / target).resolve() if target else path
            assert dest.exists(), (path.name, link)
            if anchor and dest.suffix == ".md":
                assert anchor in _markdown_anchors(dest.read_text(encoding="utf-8")), (
                    path.name,
                    link,
                )


def test_the_container_workflow_takes_only_semver_tags():
    """The semver tag rules tag vMAJOR.MINOR.PATCH(-pre) only: v1.1 would
    pass a looser check and then get no image tag at all."""
    workflow = _workflow("docker.yml")
    check = next(s for s in workflow["jobs"]["check"]["steps"] if s.get("name") == "Check the tag")
    pattern = re.search(r"grep -Eq '([^']+)'", check["run"]).group(1)
    accepted = ("v1.1.1", "v1.2.3", "v1.10.0", "v0.1.0", "v2.0.0-rc.1", "v1.1.1-beta2")
    # no hyphen inside the pre-release: the Debian and RPM versions map the
    # one after the patch number to ~ (_upstream_version)
    refused = ("v1.2.3-rc-1", "v1.1", "v1", "v1.1.1.1", "1.1.1", "v1.1.1-", "v1.1.x")
    refused += ("v01.2.3", "v1.02.3", "v1.2.03", "v1.2.3-rc..1", "v1.2.3-rc.", "v1.2.3-.rc")
    assert all(re.fullmatch(pattern, tag) for tag in accepted)
    assert not any(re.fullmatch(pattern, tag) for tag in refused)


def test_the_docs_deploy_folder_is_a_repository_variable():
    """No host path in the repository: without the variable the workflow
    stops with a clear error, before it builds anything."""
    text = (ROOT / ".forgejo" / "workflows" / "docs.yml").read_text(encoding="utf-8")
    assert "SITE_DIR: ${{ vars.DOCS_SITE_DIR }}\n" in text
    steps = _workflow("docs.yml", ".forgejo")["jobs"]["docs"]["steps"]
    first = steps[0]
    assert first["name"] == "Check the deploy folder"
    assert "DOCS_SITE_DIR" in first["run"]
    # the step itself, run with the variable unset, relative and absolute
    import subprocess

    for value, ok in (("", False), ("docs/site", False), ("~/x", False), ("/srv/docs", True)):
        done = subprocess.run(
            ["sh", "-c", first["run"]],
            env={"SITE_DIR": value, "PATH": "/usr/bin:/bin"},
            capture_output=True,
            text=True,
            check=False,
        )
        assert (done.returncode == 0) is ok, (value, done.stdout)
        assert ok or "::error::" in done.stdout
    assert not re.search(r"~/|/home/", text)  # no host path in the workflow
    assert "`DOCS_SITE_DIR`" in (ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")


def test_pngs_carry_no_dates():
    """Reproducible files: no date:create/date:modify text or tIME chunks
    (tools/screenshots.sh writes none)."""
    import struct
    import subprocess

    tracked = subprocess.run(
        ["git", "ls-files", "*.png"], cwd=ROOT, capture_output=True, text=True, check=False
    ).stdout.split()
    if not tracked:
        pytest.skip("not a git checkout")
    for name in tracked:
        data = (ROOT / name).read_bytes()
        at = 8
        while at < len(data):
            (size,) = struct.unpack(">I", data[at : at + 4])
            kind, body = data[at + 4 : at + 8], data[at + 8 : at + 8 + size]
            assert kind != b"tIME", name
            if kind in (b"tEXt", b"zTXt", b"iTXt"):
                assert not body.startswith(b"date:"), name
            at += 12 + size
    screenshots = (ROOT / "tools" / "screenshots.sh").read_text(encoding="utf-8")
    assert "png:exclude-chunks=date,time" in screenshots


def _upstream_version() -> str:
    """__version__ as the Debian and RPM packages write it: a pre-release
    1.2.0-rc.1 is 1.2.0~rc.1, which sorts before 1.2.0."""
    from tavletta import __version__

    # semver, its pre-release without hyphens: one hyphen to map, back and forth
    assert re.fullmatch(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(-[0-9A-Za-z.]+)?", __version__)
    return __version__.replace("-", "~")


def test_the_packages_are_at_the_version_of_the_source():
    """Bumped with __version__: the RPM spec's Version and its newest
    %changelog entry, debian/changelog's newest entry. The build scripts
    refuse a mismatch too, this finds it before a tag."""
    upstream = _upstream_version()
    spec = (ROOT / "packaging" / "rpm" / "tavletta.spec").read_text(encoding="utf-8")
    assert re.search(r"^Version: +(\S+)$", spec, re.M).group(1) == upstream
    release = re.search(r"^Release: +(\d+)%\{\?dist\}$", spec, re.M).group(1)
    newest = spec.split("%changelog\n", 1)[1].splitlines()[0]
    assert newest.endswith(f" - {upstream}-{release}"), newest
    changelog = (ROOT / "packaging" / "debian" / "changelog").read_text(encoding="utf-8")
    assert re.match(rf"tavletta \({re.escape(upstream)}-\d+\) ", changelog), changelog[:40]


def _flathub():
    import importlib.util

    path = ROOT / "build-aux" / "flatpak" / "flathub.py"
    spec = importlib.util.spec_from_file_location("flathub", path)
    flathub = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(flathub)
    return flathub


def test_flathub_pins_the_commit_the_mirror_has_for_the_tag():
    """--commit must be the tag's commit here and on the mirror Flathub
    fetches from; an annotated tag counts by the commit it points to."""
    import subprocess

    flathub = _flathub()
    a, b, tag_object = "a" * 40, "b" * 40, "c" * 40
    calls = []

    def git(local, remote):
        def run(args, **kwargs):
            calls.append(args)
            if args[1] == "rev-parse":
                return subprocess.CompletedProcess(args, 0 if local else 1, f"{local}\n", "")
            assert args[1:4] == ["ls-remote", "--tags", flathub.REPO_URL]
            return subprocess.CompletedProcess(args, 0, remote, "")

        return run

    annotated = f"{tag_object}\trefs/tags/v1.1.2\n{a}\trefs/tags/v1.1.2^{{}}\n"
    assert flathub.commit_of("v1.1.2", None, git(a, annotated)) == a
    assert flathub.commit_of("v1.1.2", a, git(None, f"{a}\trefs/tags/v1.1.2\n")) == a
    refused = [
        (None, git(None, annotated), "no tag v1.1.2 here"),
        (b, git(a, annotated), "is not the tag v1.1.2 here"),
        (b, git(None, annotated), f"is {a} on"),
        (None, git(a, ""), "is not on https://github.com/joshii-h/tavletta.git"),
        ("A" * 40, git(None, annotated), "not a full commit SHA"),
    ]
    for given, run, message in refused:
        with pytest.raises(ValueError, match=re.escape(message)):
            flathub.commit_of("v1.1.2", given, run)
    assert all(c[0] == "git" for c in calls)
    for tag in ("v1.1", "v01.1.2", "1.1.2", "v1.1.2-"):
        with pytest.raises(ValueError, match="not a release tag"):
            flathub.flathub_manifest("", tag, a)


def test_every_tag_check_takes_the_same_semver_tags():
    """The workflows that act on a tag (release, packages, documentation,
    container, mirror, Flatpak bundle) and flathub.py take the same tags:
    the container's semver rules, which test_the_container_workflow_takes_only_semver_tags
    pins down."""
    import itertools

    checks = {}
    for path in itertools.chain(
        (ROOT / ".forgejo" / "workflows").glob("*.yml"),
        (ROOT / ".github" / "workflows").glob("*.yml"),
    ):
        for pattern in re.findall(r"grep -Eq '(\^v[^']+)'", path.read_text(encoding="utf-8")):
            checks[f"{path.parent.parent.name}/{path.name}"] = pattern
    assert set(checks) == {
        ".forgejo/release.yml",
        ".forgejo/deb.yml",
        ".forgejo/rpm.yml",
        ".forgejo/docs.yml",
        ".github/docker.yml",
        ".github/release-mirror.yml",
        ".github/flatpak-bundle.yml",
    }
    assert len(set(checks.values())) == 1, checks
    pattern = checks[".github/docker.yml"]
    flathub = _flathub()
    for tag in ("v1.1.2", "v2.0.0-rc.1", "v2.0.0-rc-1", "v1.1", "v1.1.2.1", "v01.1.2", "1.1.2"):
        assert bool(re.fullmatch(pattern, tag)) == bool(flathub.TAG.fullmatch(tag)), tag


def test_the_screenshots_come_from_the_github_mirror_at_this_release():
    """Software centres and Flathub fetch them by these URLs: from the
    GitHub mirror (no rate limit for automated clients, as flathub.py
    explains), at the tag of this version, files that are there."""
    import xml.etree.ElementTree as ET

    from tavletta import __version__

    root = ET.parse(ROOT / "data" / "xyz.joshuahirsig.Tavletta.metainfo.xml").getroot()
    images = [image.text for image in root.iterfind("screenshots/screenshot/image")]
    assert images
    prefix = f"https://raw.githubusercontent.com/joshii-h/tavletta/v{__version__}/"
    for url in images:
        assert url.startswith(prefix), url
        assert (ROOT / url.removeprefix(prefix)).is_file(), url


def test_the_manual_page_names_every_command_and_printer():
    """data/tavletta.1, installed by the Debian and RPM packages: the
    version, every command and the printer models as the program has them."""
    from tavletta import __version__
    from tavletta.backends import backends
    from tavletta.cli import parser

    page = (ROOT / "data" / "tavletta.1").read_text(encoding="utf-8")
    minor = ".".join(__version__.split(".")[:2])
    assert re.search(
        rf'^\.TH TAVLETTA 1 \d{{4}}-\d\d-\d\d "Tavletta {re.escape(minor)}" ', page, re.M
    )
    commands = next(a for a in parser()._actions if a.dest == "command").choices
    for name in commands:
        roff = re.escape(name.replace("-", "\\-"))  # list-printers: list\-printers
        assert re.search(rf"^\.B tavletta {roff}\b", page, re.M), name
    ids = ", ".join(b.id for b in backends())
    assert ids in " ".join(page.split()), ids


def test_the_packages_install_the_print_server_disabled():
    """tavletta-server.service in the Debian and RPM packages tavletta-server:
    the package's /usr/bin/tavletta instead of a manual installation's, the
    user tavletta and the folders site/docs/server-install.md names,
    installed but not enabled, restarted by an upgrade when it runs, the
    configuration removed on purge (Debian). The program's package makes no
    user; it has the udev rule for USB label printers."""
    unit = (ROOT / "packaging" / "systemd" / "tavletta-server.service").read_text(encoding="utf-8")
    execstart = re.findall(r"^ExecStart=(\S+) serve (.*)$", unit, re.M)
    assert execstart and execstart[0][0] == "/opt/tavletta/bin/tavletta", execstart
    for path in (
        "/etc/tavletta/printers.toml",
        "/etc/tavletta/tokens.toml",
        "/var/lib/tavletta/labels",
    ):
        assert path in execstart[0][1], path
    assert re.search(r"^User=tavletta$", unit, re.M)
    rules = (ROOT / "packaging" / "debian" / "rules").read_text(encoding="utf-8")
    spec = (ROOT / "packaging" / "rpm" / "tavletta.spec").read_text(encoding="utf-8")
    for text, bindir in ((rules, "/usr/bin"), (spec, "%{_bindir}")):
        assert f"s|^ExecStart=/opt/tavletta/bin/tavletta |ExecStart={bindir}/tavletta |" in text
        assert f"grep -q '^ExecStart={bindir}/tavletta serve '" in text
        for source in ("tavletta.sysusers.conf", "70-tavletta.rules"):
            assert source in text, source
    # Debian: the service, its user and folders in tavletta-server, udev in tavletta
    targets = re.findall(r"^\s+debian/([\w-]+)/(\S+)", rules, re.M)
    installed = {path: package for package, path in targets if "python3" not in path}
    assert installed == {
        "usr/lib/systemd/system/tavletta-server.service": "tavletta-server",
        "usr/lib/sysusers.d/tavletta.conf": "tavletta-server",
        "usr/lib/tmpfiles.d/tavletta.conf": "tavletta-server",
        "usr/lib/udev/rules.d/70-tavletta.rules": "tavletta",
    }
    assert (
        "dh_installsystemd -ptavletta-server --no-enable --no-start --no-stop-on-upgrade" in rules
    )
    debian = ROOT / "packaging" / "debian"
    postinst = (debian / "tavletta-server.postinst").read_text(encoding="utf-8")
    assert '[ "$1" = "configure" ] && [ -n "$2" ]' in postinst  # an upgrade only
    assert "deb-systemd-invoke try-restart tavletta-server.service" in postinst
    postrm = (debian / "tavletta-server.postrm").read_text(encoding="utf-8")
    assert '[ "$1" = "purge" ]' in postrm and "rm -rf /etc/tavletta /var/cache/tavletta" in postrm
    assert "rmdir /var/lib/tavletta/labels /var/lib/tavletta" in postrm  # labels kept
    control = (debian / "control").read_text(encoding="utf-8")
    assert "\nPackage: tavletta-server\n" in control
    assert " tavletta (= ${binary:Version}),\n" in control.split("Package: tavletta-server")[1]
    # RPM: the subpackage server has them; the program's package makes no user
    main, server = (
        spec.split("\n%files server\n")[0].split("\n%files ")[1],
        spec.split("\n%files server\n")[1].split("\n%changelog")[0],
    )
    assert "%{_udevrulesdir}/70-tavletta.rules" in main
    for line in ("%{_unitdir}/tavletta-server.service", "%{_sysusersdir}/tavletta.conf"):
        assert line in server and line not in main, line
    assert "%systemd_post tavletta-server.service" in spec.split("%post server\n")[1]
    assert (
        "%systemd_postun_with_restart tavletta-server.service" in spec.split("%postun server\n")[1]
    )
    sysusers = (ROOT / "packaging" / "systemd" / "tavletta.sysusers.conf").read_text(
        encoding="utf-8"
    )
    assert re.search(r'^u tavletta - "[^"]+" /var/lib/tavletta$', sysusers, re.M)
    tmpfiles = (ROOT / "packaging" / "systemd" / "tavletta.tmpfiles.conf").read_text(
        encoding="utf-8"
    )
    folders = re.findall(r"^d (\S+) 0750 tavletta tavletta -$", tmpfiles, re.M)
    assert folders == ["/etc/tavletta", "/var/lib/tavletta", "/var/lib/tavletta/labels"]
    for folder in folders:  # the RPM owns them itself
        rpm = folder.replace("/etc", "%{_sysconfdir}").replace("/var/lib", "%{_sharedstatedir}")
        assert f"%dir %attr(0750,tavletta,tavletta) {rpm}\n" in server, rpm
    guide = (ROOT / "site" / "docs" / "server-install.md").read_text(encoding="utf-8")
    for path in (
        "/etc/tavletta/printers.toml",
        "/etc/tavletta/tokens.toml",
        "/var/lib/tavletta/labels/",
    ):
        assert f"`{path}`" in guide, path


def test_release_assets_get_names_github_keeps_and_a_checksum_file(tmp_path):
    """packaging/release-assets.sh collect: + and ^ (Debian and RPM
    versions) become dots, so the mirror's names are the same; clashes and
    names GitHub would change otherwise are refused."""
    import shutil
    import subprocess

    if not shutil.which("bash"):
        pytest.skip("no bash")
    script = ROOT / "packaging" / "release-assets.sh"
    built = tmp_path / "built"
    built.mkdir()
    names = [
        "python3-zint-bindings_1.2.2+ds-1_amd64.deb",
        "python3-x-0.1.0^20261105gita6f2957-1.fc44.noarch.rpm",
    ]
    for name in names:
        (built / name).write_bytes(name.encode())

    def collect(out, *files):
        return subprocess.run(
            ["bash", str(script), "collect", str(out), "SUMS", *map(str, files)],
            capture_output=True,
            text=True,
            check=False,
        )

    done = collect(tmp_path / "assets", *(built / n for n in names))
    assert done.returncode == 0, done.stderr
    kept = [
        "python3-zint-bindings_1.2.2.ds-1_amd64.deb",
        "python3-x-0.1.0.20261105gita6f2957-1.fc44.noarch.rpm",
    ]
    assert sorted(p.name for p in (tmp_path / "assets").iterdir()) == sorted([*kept, "SUMS"])
    sums = (tmp_path / "assets" / "SUMS").read_text()
    assert [line.split("  ")[1] for line in sums.splitlines()] == kept
    (built / "python3-zint-bindings_1.2.2.ds-1_amd64.deb").write_bytes(b"other")
    clash = collect(
        tmp_path / "clash", built / names[0], built / "python3-zint-bindings_1.2.2.ds-1_amd64.deb"
    )
    assert clash.returncode == 1 and "two assets named" in clash.stderr
    (built / ".hidden").write_bytes(b"")
    assert "starts or ends with a dot" in collect(tmp_path / "dot", built / ".hidden").stderr
    assert "is not empty" in collect(tmp_path / "assets", built / names[0]).stderr


class _FakeForgejo:
    """A release's assets as Forgejo's API serves them, on 127.0.0.1."""

    def __init__(self, hidden_for: int = 0):
        import http.server
        import threading

        self.assets: dict[int, tuple[str, bytes]] = {}
        self.next_id = 1
        self.hidden_for = hidden_for  # answers 404 for the release this often
        self.deleted: list[str] = []
        self.body = "Fixes.\n\n**Downloads**\n\n- pip: the sdist and the wheel"
        self.patches = 0
        fake = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _send(self, code, body=b"", kind="application/json"):
                self.send_response(code)
                self.send_header("Content-Type", kind)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                import json

                if self.path.startswith("/dl/"):
                    data = fake.assets[int(self.path[4:])][1]
                    self._send(200, data, "application/octet-stream")
                    return
                assert self.headers["Authorization"] == "token secret"
                assert self.path == "/api/v1/repos/o/r/releases/tags/v1.0.0", self.path
                if fake.hidden_for:
                    fake.hidden_for -= 1
                    self._send(404, b"{}")
                    return
                assets = [
                    {"id": i, "name": n, "browser_download_url": f"{fake.url}/dl/{i}"}
                    for i, (n, _) in fake.assets.items()
                ]
                release = {"id": 7, "body": fake.body, "assets": assets}
                self._send(200, json.dumps(release).encode())

            def do_PATCH(self):
                import json

                assert self.headers["Authorization"] == "token secret"
                assert self.path == "/api/v1/repos/o/r/releases/7"
                fake.body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))[
                    "body"
                ]
                fake.patches += 1
                self._send(200, b"{}")

            def do_DELETE(self):
                assert self.headers["Authorization"] == "token secret"
                prefix = "/api/v1/repos/o/r/releases/7/assets/"
                assert self.path.startswith(prefix)
                fake.deleted.append(fake.assets.pop(int(self.path[len(prefix) :]))[0])
                self._send(204)

            def do_POST(self):
                from email import policy
                from email.parser import BytesParser
                from urllib.parse import parse_qs, urlsplit

                assert self.headers["Authorization"] == "token secret"
                url = urlsplit(self.path)
                assert url.path == "/api/v1/repos/o/r/releases/7/assets"
                name = parse_qs(url.query)["name"][0]
                body = self.rfile.read(int(self.headers["Content-Length"]))
                head = f"Content-Type: {self.headers['Content-Type']}\r\n\r\n".encode()
                part = next(
                    BytesParser(policy=policy.default).parsebytes(head + body).iter_parts()
                )
                fake.assets[fake.next_id] = (name, part.get_payload(decode=True))
                fake.next_id += 1
                self._send(201, b"{}")

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def add(self, name: str, data: bytes) -> None:
        self.assets[self.next_id] = (name, data)
        self.next_id += 1

    def files(self) -> dict[str, bytes]:
        return dict(self.assets.values())


def test_release_assets_upload_skips_the_same_and_replaces_other_content(tmp_path):
    """packaging/release-assets.sh upload: waits for the release, skips a
    file attached with the same content, replaces one with other content
    (a rebuild, and its checksum file with it); a second run changes nothing."""
    import os
    import shutil
    import subprocess

    if not all(shutil.which(tool) for tool in ("bash", "curl", "jq", "sha256sum")):
        pytest.skip("needs bash, curl, jq and sha256sum")
    forgejo = _FakeForgejo(hidden_for=2)
    forgejo.add("same.deb", b"same")
    forgejo.add("rebuilt.deb", b"before")
    forgejo.add("other-workflow.flatpak", b"untouched")
    for name, data in (("same.deb", b"same"), ("rebuilt.deb", b"after"), ("new.deb", b"new")):
        (tmp_path / name).write_bytes(data)
    env = {
        "PATH": os.environ["PATH"],
        "FORGEJO_URL": forgejo.url,
        "GITHUB_REPOSITORY": "o/r",
        "TOKEN": "secret",
        "RELEASE_WAIT_SECONDS": "0",
    }

    def upload(*names, **extra):
        return subprocess.run(
            ["bash", str(ROOT / "packaging" / "release-assets.sh"), "upload", "v1.0.0"]
            + [str(tmp_path / n) for n in names],
            env=env | extra,
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )

    try:
        done = upload("same.deb", "rebuilt.deb", "new.deb")
        assert done.returncode == 0, done.stdout + done.stderr
        assert "same.deb exists" in done.stdout and "new.deb uploaded" in done.stdout
        assert "rebuilt.deb replaced" in done.stdout
        assert forgejo.deleted == ["rebuilt.deb"]
        assert forgejo.files() == {
            "same.deb": b"same",
            "rebuilt.deb": b"after",
            "other-workflow.flatpak": b"untouched",
            "new.deb": b"new",
        }
        again = upload("same.deb", "rebuilt.deb", "new.deb")
        assert again.returncode == 0 and again.stdout.count(" exists") == 3, again.stdout
        forgejo.hidden_for = 5
        gone = upload("new.deb", RELEASE_WAIT_TRIES="2")
        assert gone.returncode == 1 and "::error::no Forgejo release for v1.0.0" in gone.stdout
    finally:
        forgejo.server.shutdown()


def test_the_packages_name_the_project_address():
    """Maintainer, changelogs, copyright and patch headers of the Debian
    and RPM packages give the project's address, no personal one."""
    files = [p for p in (ROOT / "packaging").rglob("*") if p.is_file()]
    assert files
    for path in files:
        text = path.read_text(encoding="utf-8", errors="replace")
        assert "gmail.com" not in text, path
    control = (ROOT / "packaging" / "debian" / "control").read_text(encoding="utf-8")
    assert "Maintainer: Joshua Hirsig <tavletta@joshuahirsig.xyz>\n" in control


def test_the_package_workflows():
    """deb.yml and rpm.yml alike: the tag of a push or of a manual run
    (which then uploads too), an image pinned by digest for the job and the
    fresh container that tries the packages, uploads through
    release-assets.sh, then a line in the release notes that names the
    system the image is and the packages, then the checksum file, which
    release-mirror.yml waits for. release.yml uploads its checksum file last
    too and promises none of their files; flatpak-bundle.yml adds its line."""
    docs = _workflow("docs.yml", ".forgejo")
    release = (ROOT / ".forgejo" / "workflows" / "release.yml").read_text(encoding="utf-8")
    assert 'for f in "${files[@]}" dist/SHA256SUMS; do' in release
    for promise in (".deb", ".rpm", ".flatpak", "Debian", "Fedora"):
        assert promise not in release.split("Version and release notes")[1].split("EOF")[0]
    mirror = _workflow("release-mirror.yml")
    for name, image, sums, system in (
        ("deb.yml", "debian:trixie", "SHA256SUMS-deb", "Debian 13 (trixie)"),
        ("rpm.yml", "fedora:44", "SHA256SUMS-rpm", "Fedora 44 (`fc44`)"),
    ):
        workflow = _workflow(name, ".forgejo")
        assert set(workflow["on"]) == {"push", "workflow_dispatch"}
        (job,) = workflow["jobs"].values()
        assert re.fullmatch(re.escape(image) + r"@sha256:[0-9a-f]{64}", job["container"]["image"])
        assert workflow["env"]["IMAGE"] == job["container"]["image"], name
        assert job["env"]["TAG"] == (
            "${{ github.event_name == 'push' && github.ref_name || inputs.tag }}"
        )
        steps = {step["name"]: step for step in job["steps"]}
        code = steps["Get the code"]["run"]
        assert 'echo "PUBLISH=true"' in code.split("else")[0]  # with a tag, also a manual run
        assert '"$IMAGE"' in steps["Install and try in a fresh container"]["run"]
        assert (
            "packaging/clean-container.sh" in steps["Install and try in a fresh container"]["run"]
        )
        assert f"release-assets.sh collect assets {sums} " in steps["Assets"]["run"]
        upload = steps["Attach to the Forgejo release"]
        assert upload["if"] == "env.PUBLISH == 'true'"
        lines = [line.strip() for line in upload["run"].strip().splitlines()]
        assert lines[-4:] == [
            'src/packaging/release-assets.sh upload "$TAG" "${files[@]}"',
            'src/packaging/release-assets.sh notes "$TAG" "$NOTES"',
            "# the checksum file last: release-mirror.yml waits for it",
            f'src/packaging/release-assets.sh upload "$TAG" assets/{sums}',
        ]
        note = upload["env"]["NOTES"]
        assert note.startswith(f"- {system}, ") and sums in note, note
        assert "tavletta-server" in note  # the server's package named too
        assert workflow["env"]["DOCKER_CLI_VERSION"] == docs["env"]["DOCKER_CLI_VERSION"]
        assert re.fullmatch(r"[0-9a-f]{64}", workflow["env"]["DOCKER_CLI_SHA256"])
        assert sums in mirror["jobs"]["mirror"]["env"]["COMPLETE"].split()
    assert "SHA256SUMS" in mirror["jobs"]["mirror"]["env"]["COMPLETE"].split()
    assert "workflow_dispatch" in mirror["on"]
    bundle = (ROOT / ".github" / "workflows" / "flatpak-bundle.yml").read_text(encoding="utf-8")
    assert (
        "Tavletta-${{ github.event_name == 'push' && github.ref_name || 'test' }}-x86_64.flatpak"
        in bundle
    )
    assert (
        "Any Linux distribution with Flatpak, x86-64: \\`Tavletta-$TAG-x86_64.flatpak\\`" in bundle
    )
    assert 'packaging/release-assets.sh notes "$TAG" "$line"' in bundle


def test_the_release_notes_name_this_workflows_downloads(tmp_path):
    """release.yml's notes step, run: the metainfo's notes of this version,
    then its own downloads (the package workflows add theirs)."""
    import os
    import subprocess

    from tavletta import __version__

    step = next(
        s
        for s in _workflow("release.yml", ".forgejo")["jobs"]["release"]["steps"]
        if s["name"] == "Version and release notes"
    )
    script = step["run"].split("<<'EOF'\n", 1)[1].split("\nEOF\n", 1)[0]
    src = tmp_path / "src"
    (src / "tavletta").mkdir(parents=True)
    (src / "data").mkdir()
    (src / "tavletta" / "__init__.py").write_text(f'__version__ = "{__version__}"\n')
    metainfo = "xyz.joshuahirsig.Tavletta.metainfo.xml"
    (src / "data" / metainfo).write_bytes((ROOT / "data" / metainfo).read_bytes())
    env_file = tmp_path / "env"
    done = subprocess.run(
        [sys.executable, "-c", script],
        cwd=src,
        env=os.environ
        | {"TAG": f"v{__version__}", "DRY_RUN": "true", "GITHUB_ENV": str(env_file)},
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    body = (tmp_path / "release-notes.md").read_text()
    assert body.index("**Downloads**") > 0  # after the version's notes
    assert body.endswith(
        f"- pip: `tavletta-{__version__}.tar.gz` (the source) and the `.whl`;"
        " checksums in `SHA256SUMS`\n"
    )
    assert f"VERSION={__version__}\n" in env_file.read_text()


def test_the_package_builds_run_the_tests_as_the_ci_does():
    """CI=1 in the build and smoke scripts: a test that would skip for a
    missing tool fails instead, as on the runners (where the variable is
    set), so a missing build dependency shows in a local build too. The
    tool tests/test_icons.py needs, librsvg's introspection, is one."""
    for script in ("debian/build.sh", "debian/smoke-test.sh", "rpm/build.sh", "rpm/smoke-test.sh"):
        text = (ROOT / "packaging" / script).read_text(encoding="utf-8")
        assert "\nexport CI=1\n" in text, script
    unittests = (ROOT / "packaging" / "debian" / "tests" / "unittests").read_text(encoding="utf-8")
    assert "CI=1 FONTCONFIG_FILE=" in unittests
    control = (ROOT / "packaging" / "debian" / "control").read_text(encoding="utf-8")
    assert " gir1.2-rsvg-2.0 <!nocheck>,\n" in control
    spec = (ROOT / "packaging" / "rpm" / "tavletta.spec").read_text(encoding="utf-8")
    assert "\nBuildRequires:  librsvg2\n" in spec


def test_the_rpm_sources_are_pinned_as_the_debian_ones():
    """packaging/rpm/SHA256SUMS: one line per downloaded source, at the
    versions of the specs; zint-bindings and labelprinter with the
    checksums Debian's build.sh pins."""
    rpm = ROOT / "packaging" / "rpm"
    sums = dict(
        reversed(line.split("  ")) for line in (rpm / "SHA256SUMS").read_text().splitlines()
    )
    assert all(re.fullmatch(r"[0-9a-f]{64}", sha) for sha in sums.values())

    def version(spec):
        return re.search(r"^Version: +(\S+)$", (rpm / spec).read_text(), re.M).group(1)

    labelprinter = (rpm / "python-labelprinter.spec").read_text()
    commit = re.search(r"^%global commit ([0-9a-f]{40})$", labelprinter, re.M).group(1)
    assert set(sums) == {
        f"segno-{version('python-segno.spec')}.tar.gz",
        f"python_barcode-{version('python-barcode.spec')}.tar.gz",
        f"zint_bindings-{version('python-zint-bindings.spec')}.tar.gz",
        f"labelprinter-vc500w-{commit[:7]}.tar.gz",
    }
    debian = (ROOT / "packaging" / "debian" / "build.sh").read_text()
    pins = dict(re.findall(r"^(\w+)=(\S+)$", debian, re.M))
    assert pins["LABELPRINTER_COMMIT"] == commit
    assert sums[f"labelprinter-vc500w-{commit[:7]}.tar.gz"] == pins["LABELPRINTER_SHA256"]
    assert sums[f"zint_bindings-{pins['ZINT_VERSION']}.tar.gz"] == pins["ZINT_SHA256"]
    assert "sha256sum -c" in (rpm / "build.sh").read_text()


def test_clean_container_labels_its_containers_and_removes_stale_ones(tmp_path):
    """packaging/clean-container.sh: --rm and the label tavletta.ci=1 with
    the time made; leftovers older than three hours are removed first,
    younger ones (another job's) stay; the command's exit status is kept."""
    import os
    import shutil
    import subprocess
    import time

    if not shutil.which("bash"):
        pytest.skip("no bash")
    now = int(time.time())
    log = tmp_path / "docker.log"
    fake = tmp_path / "bin" / "docker"
    fake.parent.mkdir()
    fake.write_text(
        "#!/bin/bash\n"
        f'echo "$*" >> {log}\n'
        'case "$1" in\n'
        f"  ps) echo 'old {now - 4 * 3600}'; echo 'young {now - 600}'; echo 'unlabelled' ;;\n"
        "  create) echo new-id ;;\n"
        "  start) exit 7 ;;\n"
        "esac\n"
    )
    fake.chmod(0o755)
    (tmp_path / "work").mkdir()
    done = subprocess.run(
        [
            "bash",
            str(ROOT / "packaging" / "clean-container.sh"),
            "img@sha256:x",
            str(tmp_path / "work"),
        ]
        + ["./smoke-test.sh", "out"],
        env={"PATH": f"{fake.parent}:{os.environ['PATH']}"},
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 7, done.stderr
    calls = log.read_text().splitlines()
    assert (
        calls[0]
        == 'ps -a --filter label=tavletta.ci=1 --format {{.ID}} {{.Label "tavletta.ci.created"}}'
    )
    removed = [c for c in calls if c.startswith("rm ")]
    assert removed[0] == "rm -f old"
    assert "rm -f young" not in removed
    assert "rm -f unlabelled" in removed  # without the time: from before, stale
    create = next(c for c in calls if c.startswith("create "))
    assert create.startswith("create --rm --label tavletta.ci=1 --label tavletta.ci.created=")
    assert create.endswith("--workdir /work img@sha256:x ./smoke-test.sh out")
    assert f"cp {tmp_path / 'work'}/. new-id:/work" in calls
    assert calls[-1] == "rm -f new-id"


# USB vendor IDs (usb.ids) of the makers the backends recognise by name
USB_VENDORS = {"brother": 0x04F9, "cab": 0x0985, "dymo": 0x0922, "tsc": 0x1203, "zebra": 0x0A5F}


def test_the_udev_rule_covers_the_makers_the_backends_recognise():
    """packaging/udev/70-tavletta.rules gives the user at the computer the
    USB printers of exactly the makers the backends find by name (their
    makers and manufacturer checks), and NIIMBOT's serial port; the
    metainfo names the same hardware for software centres."""
    from tavletta.backends import backends
    from tavletta.backends.niimbot import VENDOR_IDS
    from tavletta.backends.ptouch import BROTHER

    makers = {m.casefold() for b in backends() for m in getattr(b, "makers", ())}
    for path in (ROOT / "tavletta" / "backends").glob("*.py"):
        makers |= set(
            re.findall(r'manufacturer\.casefold\(\)\.startswith\("(\w+)"\)', path.read_text())
        )
    assert makers == set(USB_VENDORS)
    assert USB_VENDORS["brother"] == BROTHER
    udev = (ROOT / "packaging" / "udev" / "70-tavletta.rules").read_text(encoding="utf-8")
    rules = [line for line in udev.splitlines() if line and not line.startswith("#")]
    assert all(line.endswith('TAG+="uaccess"') for line in rules), rules
    lp = 'SUBSYSTEM=="usbmisc", KERNEL=="lp[0-9]*", ATTRS{{idVendor}}=="{:04x}", TAG+="uaccess"'
    tty = 'SUBSYSTEM=="tty", KERNEL=="ttyACM[0-9]*", ATTRS{{idVendor}}=="{:04x}", TAG+="uaccess"'
    assert sorted(rules) == sorted(
        [lp.format(v) for v in USB_VENDORS.values()] + [tty.format(v) for v in VENDOR_IDS]
    )
    metainfo = (ROOT / "data" / "xyz.joshuahirsig.Tavletta.metainfo.xml").read_text(
        encoding="utf-8"
    )
    aliases = set(re.findall(r"<modalias>([^<]+)</modalias>", metainfo))
    assert aliases == {
        f"usb:v{v:04X}p*d*dc*dsc*dp*ic07isc01ip*in*" for v in USB_VENDORS.values()
    } | {f"usb:v{v:04X}p*" for v in VENDOR_IDS}


def test_release_assets_notes_add_a_line_once(tmp_path):
    """packaging/release-assets.sh notes: the line under **Downloads**,
    once; a re-run changes nothing; notes without the heading get it."""
    import os
    import shutil
    import subprocess

    if not all(shutil.which(tool) for tool in ("bash", "curl", "jq")):
        pytest.skip("needs bash, curl and jq")
    forgejo = _FakeForgejo()
    env = {
        "PATH": os.environ["PATH"],
        "FORGEJO_URL": forgejo.url,
        "GITHUB_REPOSITORY": "o/r",
        "TOKEN": "secret",
        "RELEASE_WAIT_SECONDS": "0",
        "NOTES_RECHECK_SECONDS": "0",
    }
    deb = "- Debian 13 (trixie), amd64: `tavletta_*_all.deb`"
    rpm = "- Fedora 44 (`fc44`), x86_64: `tavletta-*.noarch.rpm`"

    def notes(line):
        return subprocess.run(
            ["bash", str(ROOT / "packaging" / "release-assets.sh"), "notes", "v1.0.0", line],
            env=env,
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )

    try:
        assert notes(deb).returncode == 0
        assert notes(rpm).returncode == 0
        assert notes(deb).returncode == 0  # a re-run
        assert forgejo.patches == 2
        assert (
            forgejo.body
            == f"Fixes.\n\n**Downloads**\n\n- pip: the sdist and the wheel\n{deb}\n{rpm}"
        )
        forgejo.body = "Fixes."
        assert notes(deb).returncode == 0
        assert forgejo.body == f"Fixes.\n\n**Downloads**\n\n{deb}"
    finally:
        forgejo.server.shutdown()
