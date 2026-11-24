"""What the package and install.sh ship: the licence files are there."""

from __future__ import annotations

import re
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
        "python3-segno",
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
    cleanup = [c for module in manifest["modules"] for c in module.get("cleanup", [])]
    assert "/share/man/man1/segno.1" in cleanup
    for unused in ("test", "cups_backend.py", "__main__.py", "print_text.py", "queue_*.py"):
        assert any(c.endswith("labelprinter/" + unused) for c in cleanup), unused
    for module in manifest["modules"]:
        for source in module.get("sources", []):
            if source.get("type") in ("file", "archive") and "url" in source:
                assert "x-checker-data" in source, (module["name"], source["url"])
            # prebuilt binaries only as pure Python wheels (py3-none-any)
            if source.get("url", "").endswith(".whl"):
                assert source["url"].endswith("-py3-none-any.whl"), source["url"]

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
    accepted += ("v1.2.3-rc-1",)
    refused = ("v1.1", "v1", "v1.1.1.1", "1.1.1", "v1.1.1-", "v1.1.x")
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
    for tag in ("v1.1.2", "v2.0.0-rc.1", "v1.1", "v1.1.2.1", "v01.1.2", "1.1.2"):
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
