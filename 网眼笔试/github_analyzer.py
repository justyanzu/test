"""GitHub 仓库数据获取与解析。"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from github import Github
from github.GithubException import GithubException


@dataclass
class RepoAnalysis:
    full_name: str
    description: str | None
    html_url: str
    stars: int
    forks: int
    watchers: int
    open_issues: int
    size_kb: int
    default_branch: str
    license_name: str | None
    topics: list[str]
    created_at: datetime
    updated_at: datetime
    pushed_at: datetime | None
    languages: dict[str, int]
    readme_text: str | None
    is_fork: bool
    archived: bool
    visibility: str


def parse_repo_url(url: str) -> tuple[str, str]:
    """从 GitHub URL 解析 owner 与 repo 名。"""
    url = url.strip().rstrip("/")
    patterns = [
        r"github\.com[:/]+([^/]+)/([^/?#]+)",
        r"^([^/]+)/([^/]+)$",
    ]
    for pattern in patterns:
        match = re.search(pattern, url, re.IGNORECASE)
        if match:
            owner, repo = match.group(1), match.group(2)
            if repo.endswith(".git"):
                repo = repo[:-4]
            return owner, repo
    raise ValueError(
        "无法解析仓库地址，请使用形如 https://github.com/owner/repo 的链接"
    )


def _decode_readme(content_file: Any) -> str:
    raw = content_file.decoded_content
    if isinstance(raw, bytes):
        return raw.decode("utf-8", errors="replace")
    return str(raw)


def analyze_repository(url: str, token: str | None = None) -> RepoAnalysis:
    """通过 PyGithub 拉取并整理仓库指标。"""
    owner, repo_name = parse_repo_url(url)
    gh_token = token or os.environ.get("GITHUB_TOKEN")
    gh = Github(gh_token) if gh_token else Github()
    repo = gh.get_repo(f"{owner}/{repo_name}")

    readme_text: str | None = None
    try:
        readme = repo.get_readme()
        readme_text = _decode_readme(readme)
    except GithubException:
        readme_text = None

    license_name = repo.license.name if repo.license else None
    # GitHub Languages API：与网页右侧 Languages 栏相同，由 Linguist 按代码字节统计
    languages: dict[str, int] = {}
    for lang, bytes_count in repo.get_languages().items():
        if lang == "url":
            continue
        if isinstance(bytes_count, (int, float)) and not isinstance(bytes_count, bool):
            languages[lang] = int(bytes_count)

    visibility = "public"
    if getattr(repo, "private", False):
        visibility = "private"

    return RepoAnalysis(
        full_name=repo.full_name,
        description=repo.description,
        html_url=repo.html_url,
        stars=repo.stargazers_count,
        forks=repo.forks_count,
        watchers=repo.watchers_count,
        open_issues=repo.open_issues_count,
        size_kb=repo.size,
        default_branch=repo.default_branch,
        license_name=license_name,
        topics=list(repo.get_topics()),
        created_at=repo.created_at,
        updated_at=repo.updated_at,
        pushed_at=repo.pushed_at,
        languages=languages,
        readme_text=readme_text,
        is_fork=repo.fork,
        archived=repo.archived,
        visibility=visibility,
    )
