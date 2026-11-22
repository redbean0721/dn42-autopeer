import logging
import os
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)

_CREDENTIAL_HELPER = (
    '!f() { test "$1" = get || exit 0; '
    'echo "username=${REGISTRY_GIT_USERNAME}"; echo "password=${REGISTRY_GIT_TOKEN}"; }; f'
)


class GitError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class Revision:
    commit: str
    commit_time: datetime


class RegistryRepo:
    def __init__(
        self,
        path: Path,
        url: str,
        branch: str,
        timeout: float,
        username: str,
        token: str,
    ) -> None:
        self.path = path
        self.url = url
        self.branch = branch
        self.timeout = timeout
        self._username = username
        self._token = token

    def _env(self) -> dict[str, str]:
        env = {
            k: v
            for k, v in os.environ.items()
            if k in ("PATH", "LANG", "LC_ALL", "SSL_CERT_FILE", "SSL_CERT_DIR")
            or k.endswith("_PROXY")
            or k.endswith("_proxy")
        }
        env.update(
            GIT_TERMINAL_PROMPT="0",
            GIT_CONFIG_NOSYSTEM="1",
            GIT_CONFIG_GLOBAL=os.devnull,
            HOME=str(self.path.parent),
        )
        config = [
            ("gc.auto", "0"),
            ("credential.helper", ""),
            ("credential.helper", _CREDENTIAL_HELPER),
        ]
        env["GIT_CONFIG_COUNT"] = str(len(config))
        for i, (key, value) in enumerate(config):
            env[f"GIT_CONFIG_KEY_{i}"] = key
            env[f"GIT_CONFIG_VALUE_{i}"] = value
        env["REGISTRY_GIT_USERNAME"] = self._username
        env["REGISTRY_GIT_TOKEN"] = self._token
        return env

    def _git(self, *args: str, cwd: Path | None = None) -> str:
        cmd = ["git", *args]
        try:
            proc = subprocess.run(
                cmd,
                cwd=cwd,
                env=self._env(),
                capture_output=True,
                text=True,
                timeout=self.timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise GitError(f"git {args[0]} timed out after {self.timeout}s") from exc
        if proc.returncode != 0:
            raise GitError(f"git {args[0]} failed ({proc.returncode}): {proc.stderr.strip()}")
        return proc.stdout.strip()

    def _is_valid_clone(self) -> bool:
        if not (self.path / ".git").is_dir():
            return False
        try:
            remote = self._git("remote", "get-url", "origin", cwd=self.path)
        except GitError:
            return False
        return remote == self.url

    def _clone(self) -> None:
        if self.path.exists():
            log.warning("removing invalid checkout at %s", self.path)
            shutil.rmtree(self.path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        log.info("cloning %s (%s)", self.url, self.branch)
        self._git(
            "clone",
            "--depth=1",
            "--single-branch",
            "--no-tags",
            f"--branch={self.branch}",
            self.url,
            str(self.path),
        )

    def fetch(self) -> Revision:
        if not self._is_valid_clone():
            self._clone()
        self._git("fetch", "--depth=1", "--no-tags", "origin", self.branch, cwd=self.path)
        out = self._git("log", "-1", "--format=%H %cI", "FETCH_HEAD", cwd=self.path)
        commit, _, ts = out.partition(" ")
        return Revision(commit=commit, commit_time=datetime.fromisoformat(ts))

    def checkout(self, commit: str) -> None:
        self._git("reset", "--hard", "--quiet", commit, cwd=self.path)
        self._git("clean", "-ffdxq", cwd=self.path)

    def gc(self) -> None:
        self._git("reflog", "expire", "--expire=now", "--all", cwd=self.path)
        self._git("gc", "--prune=now", "--quiet", cwd=self.path)
