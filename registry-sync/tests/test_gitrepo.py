import subprocess
from pathlib import Path

import pytest

from registry_sync.gitrepo import GitError, RegistryRepo

from .conftest import git


def make_repo(tmp_path: Path, url: str, token: str = "tok") -> RegistryRepo:
    return RegistryRepo(
        path=tmp_path / "work" / "registry",
        url=url,
        branch="master",
        timeout=60,
        username="u",
        token=token,
    )


def test_clone_fetch_checkout(tmp_path: Path, remote_repo: Path) -> None:
    repo = make_repo(tmp_path, f"file://{remote_repo}")
    rev = repo.fetch()
    assert rev.commit == git(remote_repo, "rev-parse", "HEAD")
    repo.checkout(rev.commit)
    assert (repo.path / "data" / "mntner" / "FOO-MNT").is_file()

    (remote_repo / "data" / "mntner" / "NEW-MNT").write_text("mntner: NEW-MNT\n")
    git(remote_repo, "add", "-A")
    git(remote_repo, "commit", "-q", "-m", "add")
    rev2 = repo.fetch()
    assert rev2.commit != rev.commit
    assert not (repo.path / "data" / "mntner" / "NEW-MNT").exists()
    repo.checkout(rev2.commit)
    assert (repo.path / "data" / "mntner" / "NEW-MNT").is_file()

    (repo.path / "data" / "junk").write_text("x")
    (repo.path / "data" / "mntner" / "FOO-MNT").write_text("tampered")
    repo.checkout(rev2.commit)
    assert not (repo.path / "data" / "junk").exists()
    assert "tampered" not in (repo.path / "data" / "mntner" / "FOO-MNT").read_text()
    repo.gc()


def test_reclones_when_dir_is_not_a_repo(tmp_path: Path, remote_repo: Path) -> None:
    repo = make_repo(tmp_path, f"file://{remote_repo}")
    repo.path.mkdir(parents=True)
    (repo.path / "leftover").write_text("x")
    repo.fetch()
    assert (repo.path / ".git").is_dir()
    assert not (repo.path / "leftover").exists()


def test_reclones_when_remote_url_changes(tmp_path: Path, remote_repo: Path) -> None:
    make_repo(tmp_path, f"file://{remote_repo}").fetch()
    other = tmp_path / "other"
    git(tmp_path, "clone", "-q", str(remote_repo), str(other))
    repo2 = make_repo(tmp_path, f"file://{other}")
    repo2.fetch()
    assert git(repo2.path, "remote", "get-url", "origin") == f"file://{other}"


def test_token_is_not_written_to_disk(tmp_path: Path, remote_repo: Path) -> None:
    repo = make_repo(tmp_path, f"file://{remote_repo}", token="s3cr3t-token")
    repo.fetch()
    for path in repo.path.joinpath(".git").rglob("*"):
        if path.is_file():
            assert b"s3cr3t-token" not in path.read_bytes(), path


def test_fetch_failure_raises(tmp_path: Path) -> None:
    repo = make_repo(tmp_path, f"file://{tmp_path}/nope")
    with pytest.raises(GitError):
        repo.fetch()


def test_credential_helper_supplies_token(tmp_path: Path) -> None:
    repo = make_repo(tmp_path, "https://example.invalid/r.git")
    proc = subprocess.run(
        ["git", "credential", "fill"],
        input="protocol=https\nhost=example.invalid\n\n",
        env=repo._env(),
        capture_output=True,
        text=True,
        check=True,
    )
    assert "username=u\n" in proc.stdout
    assert "password=tok\n" in proc.stdout
