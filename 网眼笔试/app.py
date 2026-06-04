"""GitHub 仓库体检 — DeepSeek 风格会话式界面。"""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from github.GithubException import (
    BadCredentialsException,
    GithubException,
    RateLimitExceededException,
)

from ai_analyzer import analyze_repo_with_ai
from github_analyzer import RepoAnalysis, analyze_repository, parse_repo_url
from readme_processor import strip_readme_images

CHAT_WIDTH = "min(768px, 100%)"
TITLE_MAX_LEN = 42
SIDEBAR_LABEL_MAX = 28
UI_BORDER = "rgba(49, 51, 63, 0.18)"
UI_SURFACE = "#ffffff"
UI_MUTED_BG = "rgba(49, 51, 63, 0.06)"


@dataclass
class AnalysisOutcome:
    kind: Literal["ok", "error"]
    result: RepoAnalysis | None = None
    error: str | None = None


def _url_cache_key(url: str) -> str | None:
    """将 URL 规范为 owner/repo（小写），作为缓存键。"""
    try:
        owner, repo = parse_repo_url(url)
        return f"{owner}/{repo}".lower()
    except ValueError:
        return None


def _repo_to_dict(repo: RepoAnalysis) -> dict:
    return {
        "full_name": repo.full_name,
        "description": repo.description,
        "html_url": repo.html_url,
        "stars": repo.stars,
        "forks": repo.forks,
        "watchers": repo.watchers,
        "open_issues": repo.open_issues,
        "size_kb": repo.size_kb,
        "default_branch": repo.default_branch,
        "license_name": repo.license_name,
        "topics": repo.topics,
        "created_at": repo.created_at.isoformat(),
        "updated_at": repo.updated_at.isoformat(),
        "pushed_at": repo.pushed_at.isoformat() if repo.pushed_at else None,
        "languages": repo.languages,
        "readme_text": repo.readme_text,
        "is_fork": repo.is_fork,
        "archived": repo.archived,
        "visibility": repo.visibility,
    }


def _repo_from_dict(data: dict) -> RepoAnalysis:
    pushed = data.get("pushed_at")
    return RepoAnalysis(
        full_name=data["full_name"],
        description=data.get("description"),
        html_url=data["html_url"],
        stars=data["stars"],
        forks=data["forks"],
        watchers=data["watchers"],
        open_issues=data["open_issues"],
        size_kb=data["size_kb"],
        default_branch=data["default_branch"],
        license_name=data.get("license_name"),
        topics=list(data.get("topics") or []),
        created_at=datetime.fromisoformat(data["created_at"]),
        updated_at=datetime.fromisoformat(data["updated_at"]),
        pushed_at=datetime.fromisoformat(pushed) if pushed else None,
        languages=dict(data.get("languages") or {}),
        readme_text=data.get("readme_text"),
        is_fork=bool(data.get("is_fork")),
        archived=bool(data.get("archived")),
        visibility=data.get("visibility") or "public",
    )


def _cache_track_id(
    url_key: str,
    token: str | None,
    ai_enabled: bool,
    api_key: str | None,
) -> tuple[str, str, bool, str]:
    return (url_key, token or "", ai_enabled, api_key or "")


def _is_analysis_cache_hit(track_id: tuple[str, str, bool, str]) -> bool:
    return track_id in st.session_state.get("analysis_cache_ids", set())


def _mark_analysis_cache_hit(track_id: tuple[str, str, bool, str]) -> None:
    st.session_state.setdefault("analysis_cache_ids", set()).add(track_id)


# ---------------------------------------------------------------------------
# 多会话状态
# ---------------------------------------------------------------------------
def _new_chat_id() -> str:
    return uuid.uuid4().hex[:10]


def _now_iso() -> str:
    return datetime.now().isoformat()


def _repo_title_text(url: str) -> str:
    """将 URL 转为 owner/repo，便于侧边栏展示。"""
    text = url.strip()
    if not text:
        return ""
    try:
        owner, repo = parse_repo_url(text)
        return f"{owner}/{repo}"
    except ValueError:
        return text


def _ellipsis(text: str, max_len: int) -> str:
    if len(text) <= max_len:
        return text
    return text[: max_len - 1] + "…"


def _short_title(url: str) -> str:
    return _ellipsis(_repo_title_text(url), TITLE_MAX_LEN)


def _sidebar_button_label(title: str, *, active: bool) -> str:
    label = _ellipsis(title or "未命名仓库", SIDEBAR_LABEL_MAX)
    return f"▸ {label}" if active else label


def _new_session_record() -> dict:
    return {
        "title": "",
        "messages": [],
        "updated_at": _now_iso(),
        "title_locked": False,
    }


def init_chat_state() -> None:
    if "chat_sessions" not in st.session_state:
        st.session_state.chat_sessions = {}
        st.session_state.active_chat_id = None

    # 兼容旧版单会话数据
    if st.session_state.get("messages"):
        chat_id = _new_chat_id()
        st.session_state.chat_sessions[chat_id] = _new_session_record()
        st.session_state.chat_sessions[chat_id]["messages"] = st.session_state.messages
        for msg in st.session_state.messages:
            if msg.get("role") == "user" and msg.get("content"):
                st.session_state.chat_sessions[chat_id]["title"] = _short_title(
                    msg["content"]
                )
                st.session_state.chat_sessions[chat_id]["title_locked"] = True
                break
        st.session_state.chat_sessions[chat_id]["updated_at"] = _now_iso()
        st.session_state.active_chat_id = chat_id
        del st.session_state["messages"]

    # 清理无消息的空会话（含旧版默认「新对话」）
    empty_ids = [
        cid
        for cid, s in st.session_state.chat_sessions.items()
        if not s["messages"]
    ]
    for cid in empty_ids:
        del st.session_state.chat_sessions[cid]
    if (
        st.session_state.active_chat_id
        and st.session_state.active_chat_id not in st.session_state.chat_sessions
    ):
        st.session_state.active_chat_id = None


def get_active_messages() -> list[dict]:
    chat_id = st.session_state.active_chat_id
    if not chat_id or chat_id not in st.session_state.chat_sessions:
        return []
    return st.session_state.chat_sessions[chat_id]["messages"]


def start_new_workspace() -> None:
    """进入空白工作区，不创建侧边栏条目。"""
    st.session_state.active_chat_id = None


def ensure_chat_for_input() -> str:
    """发送首条消息时创建会话并写入侧边栏。"""
    chat_id = st.session_state.active_chat_id
    if chat_id and chat_id in st.session_state.chat_sessions:
        return chat_id
    chat_id = _new_chat_id()
    st.session_state.chat_sessions[chat_id] = _new_session_record()
    st.session_state.active_chat_id = chat_id
    return chat_id


def delete_chat(chat_id: str) -> None:
    sessions = st.session_state.chat_sessions
    if chat_id not in sessions:
        return
    del sessions[chat_id]
    if st.session_state.active_chat_id == chat_id:
        if sessions:
            st.session_state.active_chat_id = max(
                sessions.keys(),
                key=lambda k: sessions[k]["updated_at"],
            )
        else:
            st.session_state.active_chat_id = None


def visible_chat_ids() -> list[str]:
    sessions = st.session_state.chat_sessions
    return sorted(
        [cid for cid, s in sessions.items() if s["messages"]],
        key=lambda cid: sessions[cid]["updated_at"],
        reverse=True,
    )


def touch_chat(chat_id: str) -> None:
    st.session_state.chat_sessions[chat_id]["updated_at"] = _now_iso()


def set_title_from_first_url(chat_id: str, url: str) -> None:
    session = st.session_state.chat_sessions[chat_id]
    if session["title_locked"]:
        return
    session["title"] = _short_title(url)
    session["title_locked"] = True


def append_user_message(chat_id: str, content: str) -> None:
    session = st.session_state.chat_sessions[chat_id]
    session["messages"].append(
        {"role": "user", "content": content.strip(), "id": _new_chat_id()}
    )
    set_title_from_first_url(chat_id, content)
    touch_chat(chat_id)


def append_assistant_message(
    chat_id: str,
    *,
    result: RepoAnalysis | None = None,
    error: str | None = None,
    ai_enabled: bool = False,
    ai_summary: str | None = None,
) -> None:
    session = st.session_state.chat_sessions[chat_id]
    msg: dict = {
        "role": "assistant",
        "id": _new_chat_id(),
        "ai_enabled": ai_enabled,
    }
    if error:
        msg["error"] = error
    elif result:
        msg["result"] = result
    if ai_summary:
        msg["ai_summary"] = ai_summary
    session["messages"].append(msg)
    touch_chat(chat_id)


# ---------------------------------------------------------------------------
# Page config（需最先执行）
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="GitHub 仓库体检",
    page_icon="🔍",
    layout="centered",
    initial_sidebar_state="expanded",
)

init_chat_state()

if "ai_analysis_enabled" not in st.session_state:
    st.session_state.ai_analysis_enabled = True


def _github_token() -> str | None:
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if token:
        return token
    try:
        return str(st.secrets["GITHUB_TOKEN"]).strip() or None
    except (KeyError, FileNotFoundError, AttributeError, TypeError):
        return None


def _deepseek_api_key() -> str | None:
    key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if key:
        return key
    try:
        return str(st.secrets["DEEPSEEK_API_KEY"]).strip() or None
    except (KeyError, FileNotFoundError, AttributeError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Global styles
# ---------------------------------------------------------------------------
st.markdown(
    f"""
    <style>
    .block-container {{
        max-width: {CHAT_WIDTH};
        padding-top: 3.25rem;
    }}
    /* 会话区与顶栏留出间距，避免首条用户消息被遮挡 */
    .chat-thread {{
        padding-top: 0.75rem;
    }}
    .chat-thread > div[data-testid="stChatMessage"]:first-child,
    .chat-thread .user-msg-row:first-child {{
        margin-top: 0.5rem;
    }}
    /* 用户消息：右侧简洁圆角气泡，无头像 */
    .user-msg-row {{
        display: flex;
        justify-content: flex-end;
        margin: 0.85rem 0 1.1rem;
        padding-right: 0.15rem;
    }}
    .user-msg-bubble {{
        display: inline-block;
        background: {UI_SURFACE};
        border: 1px solid {UI_BORDER};
        border-radius: 999px;
        padding: 0.5rem 1rem;
        max-width: 92%;
        font-size: 0.9rem;
        line-height: 1.45;
        color: #31333f;
        box-shadow: 0 2px 12px rgba(0, 0, 0, 0.06);
        word-break: break-all;
    }}
    /* 助手消息去掉默认头像占位 */
    .chat-thread [data-testid="stChatMessage"]:has([aria-label="Chat message from assistant"]) [data-testid="stChatMessageAvatar"] {{
        display: none !important;
    }}
    .chat-thread [data-testid="stChatMessage"]:has([aria-label="Chat message from assistant"]) {{
        gap: 0 !important;
    }}
    .chat-thread [data-testid="stChatMessage"] {{
        margin-bottom: 0.5rem;
        overflow: visible !important;
        max-height: none !important;
        height: auto !important;
    }}
    .chat-thread [data-testid="stChatMessageContent"] {{
        overflow: visible !important;
        max-height: none !important;
        height: auto !important;
    }}
    /* AI 评级放在 chat_message 外，避免与图表同容器被裁切 */
    .ai-rating-outer {{
        margin: 0.25rem 0 1.25rem 0;
        padding: 0 0.25rem;
        overflow: visible !important;
        max-height: none !important;
    }}
    .ai-rating-outer .ai-rating-title {{
        font-size: 1.1rem;
        font-weight: 600;
        margin: 0 0 0.35rem;
        color: #31333f;
    }}
    /* 模型输出的 ### 小标题（Streamlit 渲染为 h3） */
    .ai-rating-outer ~ div h3 {{
        font-size: 0.95rem !important;
        font-weight: 600;
        margin: 0.85rem 0 0.3rem !important;
    }}
    .ai-rating-outer ~ div p {{
        line-height: 1.65;
    }}
    /* 语言分布图容器，避免图例与下文重叠 */
    .lang-chart-wrap {{
        margin: 0.5rem 0 1rem;
        overflow: visible;
    }}
    .lang-chart-wrap [data-testid="stPlotlyChart"] {{
        overflow: visible !important;
    }}
    .chat-welcome {{
        min-height: 48vh;
        display: flex;
        flex-direction: column;
        align-items: center;
        justify-content: center;
        text-align: center;
        padding: 2rem 0.5rem;
    }}
    .chat-welcome h1 {{
        font-size: 2rem;
        font-weight: 700;
        margin: 0 0 0.5rem;
    }}
    .chat-welcome p {{
        color: rgba(49, 51, 63, 0.72);
        font-size: 1rem;
        margin: 0;
        line-height: 1.6;
    }}
    [data-testid="stChatMessage"] [data-testid="stChatMessageContent"] {{
        max-width: 100%;
        width: 100%;
    }}
    /* 首屏：居中输入框 + 下方 AI 开关（文档流，非 fixed） */
    .center-search-anchor + div[data-testid="stVerticalBlockBorderWrapper"] {{
        max-width: 680px;
        margin: 0.75rem auto 0;
        border: 1px solid {UI_BORDER};
        border-radius: 999px;
        padding: 0.35rem 0.5rem 0.35rem 1rem;
        background: {UI_SURFACE};
        box-shadow: 0 2px 16px rgba(0, 0, 0, 0.06);
    }}
    .center-search-anchor + div [data-testid="stHorizontalBlock"] {{
        align-items: center !important;
        gap: 0.15rem !important;
    }}
    .center-search-anchor + div input {{
        border: none !important;
        box-shadow: none !important;
        background: transparent !important;
        padding-left: 0 !important;
    }}
    .center-search-anchor + div label {{
        display: none;
    }}
    .center-search-anchor + div button {{
        border-radius: 999px !important;
        min-width: 2.75rem;
        height: 2.75rem;
        font-size: 1.2rem;
        border: none !important;
        background: {UI_MUTED_BG} !important;
        color: #31333f !important;
    }}
    /* 侧边栏 AI 开关 */
    [data-testid="stSidebar"] .sidebar-ai-toggle label {{
        font-size: 0.88rem !important;
        font-weight: 600;
    }}
    /* 首屏隐藏底部 chat_input；有会话后显示 */
    body.welcome-mode [data-testid="stBottomBlock"] {{
        display: none !important;
    }}
    body.chat-active [data-testid="stBottomBlock"] {{
        display: block !important;
        position: fixed !important;
        left: 0 !important;
        right: 0 !important;
        bottom: 0 !important;
        background: linear-gradient(transparent, var(--background-color, #fff) 32%);
        padding: 0.4rem 0 1rem !important;
        z-index: 999;
    }}
    body.welcome-mode .block-container {{
        padding-bottom: 3rem;
    }}
    body.chat-active .block-container {{
        padding-bottom: 6.5rem;
    }}
    [data-testid="stBottomBlock"] > div {{
        max-width: {CHAT_WIDTH};
        margin: 0 auto;
        padding: 0 1rem 0 1rem;
    }}
    [data-testid="stChatInput"] {{
        border-radius: 1.5rem !important;
        border: 1px solid {UI_BORDER} !important;
        box-shadow: 0 2px 18px rgba(0, 0, 0, 0.08);
        background: {UI_SURFACE} !important;
    }}
    [data-testid="stChatInput"] button {{
        background: transparent !important;
        color: #31333f !important;
        border: none !important;
    }}
    [data-testid="stChatInput"] button:hover {{
        background: {UI_MUTED_BG} !important;
        border-radius: 0.5rem !important;
    }}
    /* 侧边栏 DeepSeek 风格 */
    [data-testid="stSidebar"] {{
        background-color: #f7f7f8;
    }}
    [data-testid="stSidebar"] .sidebar-brand {{
        font-size: 1.05rem;
        font-weight: 600;
        padding: 0.25rem 0 1rem;
    }}
    [data-testid="stSidebar"] .history-label {{
        font-size: 0.75rem;
        color: rgba(49, 51, 63, 0.55);
        margin: 0.5rem 0 0.35rem;
        letter-spacing: 0.02em;
    }}
    /* 历史会话：单行卡片，标题省略 + 内嵌删除 */
    div.session-item-anchor + div {{
        margin-bottom: 0.4rem;
        border: 1px solid {UI_BORDER};
        border-radius: 0.625rem;
        background: {UI_SURFACE};
        padding: 0.1rem 0.15rem;
        box-shadow: 0 1px 4px rgba(0, 0, 0, 0.04);
    }}
    div.session-item-anchor.active + div {{
        box-shadow: 0 2px 12px rgba(0, 0, 0, 0.07);
    }}
    div.session-item-anchor + div [data-testid="stHorizontalBlock"] {{
        align-items: center !important;
        gap: 0 !important;
    }}
    div.session-item-anchor + div [data-testid="stHorizontalBlock"] [data-testid="column"] {{
        display: flex !important;
        align-items: center !important;
    }}
    div.session-item-anchor + div [data-testid="column"]:first-child {{
        min-width: 0;
        flex: 1 1 auto;
    }}
    div.session-item-anchor + div [data-testid="column"]:last-child {{
        flex: 0 0 auto;
        width: 2.1rem !important;
        max-width: 2.1rem !important;
    }}
    div.session-item-anchor + div [data-testid="stHorizontalBlock"] button {{
        font-size: 0.86rem;
        color: #31333f;
        min-height: 2.35rem;
        height: 2.35rem;
    }}
    div.session-item-anchor + div [data-testid="column"]:first-child button {{
        border: none !important;
        background: transparent !important;
        text-align: left;
        justify-content: flex-start;
        padding: 0 0.5rem 0 0.55rem !important;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
        width: 100%;
        box-shadow: none !important;
    }}
    div.session-item-anchor + div [data-testid="column"]:first-child button:hover {{
        background: {UI_MUTED_BG} !important;
        border-radius: 0.5rem 0 0 0.5rem !important;
    }}
    div.session-item-anchor + div [data-testid="column"]:last-child button {{
        border: none !important;
        background: transparent !important;
        color: rgba(49, 51, 63, 0.45) !important;
        padding: 0 !important;
        min-width: 2rem !important;
        width: 2rem !important;
        font-size: 1rem !important;
        line-height: 1;
        box-shadow: none !important;
    }}
    div.session-item-anchor + div [data-testid="column"]:last-child button:hover {{
        background: {UI_MUTED_BG} !important;
        color: #31333f !important;
        border-radius: 0 0.5rem 0.5rem 0 !important;
    }}
    div[data-testid="stSidebar"] > div > button[kind="secondary"] {{
        border: 1px solid {UI_BORDER};
        background: {UI_SURFACE};
        border-radius: 0.625rem;
        font-weight: 600;
    }}
    div[data-testid="stSidebar"] button[kind="secondary"]:hover {{
        background: {UI_MUTED_BG};
    }}
    div[data-testid="stSidebar"] button[kind="primary"] {{
        background: {UI_SURFACE} !important;
        color: #31333f !important;
        border: 1px solid {UI_BORDER} !important;
        box-shadow: 0 2px 12px rgba(0, 0, 0, 0.06) !important;
    }}
    div[data-testid="stSidebar"] button[kind="primary"]:hover {{
        background: {UI_MUTED_BG} !important;
        color: #31333f !important;
        border: 1px solid {UI_BORDER} !important;
    }}
    </style>
    """,
    unsafe_allow_html=True,
)


def _format_dt(dt) -> str:
    if dt is None:
        return "—"
    return dt.strftime("%Y-%m-%d %H:%M UTC")


def fetch_analysis(url: str, token: str | None) -> AnalysisOutcome:
    url = url.strip()
    if not url:
        return AnalysisOutcome(kind="error", error="请输入 GitHub 仓库 URL。")

    try:
        result = analyze_repository(url, token=token or None)
    except ValueError as e:
        return AnalysisOutcome(kind="error", error=str(e))
    except BadCredentialsException:
        return AnalysisOutcome(
            kind="error",
            error="GitHub Token 无效。请设置环境变量 GITHUB_TOKEN 或 secrets.toml。",
        )
    except RateLimitExceededException:
        return AnalysisOutcome(
            kind="error",
            error="API 请求频率已达上限。请配置 GITHUB_TOKEN 后重试。",
        )
    except GithubException as e:
        status = getattr(e, "status", None)
        if status == 404:
            return AnalysisOutcome(kind="error", error="仓库不存在或不是公开仓库，请检查 URL。")
        msg = e.data.get("message", str(e)) if e.data else str(e)
        return AnalysisOutcome(kind="error", error=f"GitHub API 错误：{msg}")
    except Exception as e:
        return AnalysisOutcome(kind="error", error=f"分析失败：{e}")
    else:
        return AnalysisOutcome(kind="ok", result=result)


@st.cache_data(show_spinner=False)
def _cached_load_analysis(
    url_key: str,
    token: str | None,
    ai_enabled: bool,
    api_key: str | None,
) -> dict:
    """按 owner/repo 缓存 GitHub + DeepSeek 结果；相同参数不会重复请求 API。"""
    github_url = f"https://github.com/{url_key}"
    outcome = fetch_analysis(github_url, token)

    if outcome.kind != "ok" or not outcome.result:
        return {
            "kind": "error",
            "error": outcome.error or "分析失败",
            "result": None,
            "ai_enabled": False,
            "ai_summary": None,
        }

    repo = outcome.result
    ai_summary: str | None = None
    actual_ai_enabled = ai_enabled

    if ai_enabled:
        if api_key:
            readme_plain = strip_readme_images(repo.readme_text)
            try:
                ai_summary = analyze_repo_with_ai(
                    repo, readme_plain, api_key=api_key
                )
            except Exception as e:
                ai_summary = f"AI 分析失败：{e}"
        else:
            ai_summary = (
                "未配置 **DEEPSEEK_API_KEY**（环境变量或 `.streamlit/secrets.toml`），"
                "无法调用 AI 分析。"
            )

    return {
        "kind": "ok",
        "error": None,
        "result": _repo_to_dict(repo),
        "ai_enabled": actual_ai_enabled,
        "ai_summary": ai_summary,
    }


def _bundle_to_outcome(bundle: dict) -> AnalysisOutcome:
    if bundle["kind"] == "error":
        return AnalysisOutcome(kind="error", error=bundle.get("error") or "分析失败")
    result = bundle.get("result")
    if not result:
        return AnalysisOutcome(kind="error", error="分析失败")
    return AnalysisOutcome(kind="ok", result=_repo_from_dict(result))


def render_metrics(data: RepoAnalysis) -> None:
    st.markdown(f"### 📦 {data.full_name}")
    if data.description:
        st.caption(data.description)
    st.link_button("在 GitHub 打开", data.html_url, width="content")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("⭐ Stars", f"{data.stars:,}")
    c2.metric("🍴 Forks", f"{data.forks:,}")
    c3.metric("👀 Watchers", f"{data.watchers:,}")
    c4.metric("📋 Open Issues", f"{data.open_issues:,}")

    c5, c6, c7, c8 = st.columns(4)
    c5.metric("📁 体积 (KB)", f"{data.size_kb:,}")
    c6.metric("🌿 默认分支", data.default_branch)
    c7.metric("📜 许可证", data.license_name or "未声明")
    c8.metric("🔒 可见性", data.visibility)

    tags = []
    if data.is_fork:
        tags.append("Fork")
    if data.archived:
        tags.append("已归档")
    if data.topics:
        tags.extend(data.topics[:8])
    if tags:
        st.markdown("**标签** · " + " ".join(f"`{t}`" for t in tags))

    st.caption(
        f"创建 {_format_dt(data.created_at)} · "
        f"更新 {_format_dt(data.updated_at)} · "
        f"推送 {_format_dt(data.pushed_at)}"
    )


def render_engagement_chart(data: RepoAnalysis, *, element_id: str) -> None:
    df = pd.DataFrame(
        {
            "指标": ["Stars", "Forks", "Watchers", "Open Issues"],
            "数量": [data.stars, data.forks, data.watchers, data.open_issues],
        }
    )
    fig = px.bar(df, x="指标", y="数量", title="社区互动", color="指标", text="数量")
    fig.update_traces(texttemplate="%{text:,}", textposition="outside")
    fig.update_layout(showlegend=False, height=320, margin=dict(t=40, b=20))
    st.plotly_chart(fig, width="stretch", key=f"eng_chart_{element_id}")


def _language_df(data: RepoAnalysis) -> pd.DataFrame | None:
    if not data.languages:
        return None
    df = pd.DataFrame(
        {"语言": list(data.languages.keys()), "字节数": list(data.languages.values())}
    )
    df["字节数"] = pd.to_numeric(df["字节数"], errors="coerce").fillna(0)
    total = df["字节数"].sum()
    if total <= 0:
        return None
    df["占比 %"] = (df["字节数"] / total * 100).round(1)
    return df


def render_language_github_bar(data: RepoAnalysis, *, element_id: str) -> None:
    df = _language_df(data)
    if df is None:
        st.warning("暂无语言统计数据。")
        return

    st.caption("语言占比与 GitHub 仓库页 Languages 栏同源（Linguist 按代码字节统计）。")
    df = df.sort_values("字节数", ascending=False)
    palette = px.colors.qualitative.Vivid
    lang_count = len(df)

    fig = go.Figure()
    for i, row in df.iterrows():
        lang, pct, nbytes = row["语言"], row["占比 %"], int(row["字节数"])
        fig.add_trace(
            go.Bar(
                y=[""],
                x=[pct],
                name=lang,
                orientation="h",
                marker_color=palette[i % len(palette)],
                hovertemplate=(
                    f"<b>{lang}</b><br>字节：{nbytes:,}<br>{pct}%<extra></extra>"
                ),
            )
        )

    # 图例改为下方文本展示，避免 Plotly 图例与上下内容重叠
    fig.update_layout(
        barmode="stack",
        title=dict(text="Languages", font=dict(size=14)),
        xaxis=dict(range=[0, 100], ticksuffix="%", showgrid=False),
        yaxis=dict(visible=False),
        height=72,
        margin=dict(l=8, r=8, t=36, b=8),
        showlegend=False,
    )

    st.plotly_chart(
        fig,
        width="stretch",
        key=f"lang_chart_{element_id}",
    )

    legend_cols = st.columns(min(lang_count, 4) or 1)
    for idx, row in enumerate(df.itertuples(index=False)):
        col = legend_cols[idx % len(legend_cols)]
        lang, pct = row[0], row[2]  # 语言, 占比 %
        col.markdown(
            f"<span style='font-size:0.82rem;color:#555'>● {lang} **{pct}%**</span>",
            unsafe_allow_html=True,
        )


def render_repo_metrics_and_charts(data: RepoAnalysis, *, element_id: str) -> None:
    """仓库指标与图表（置于 chat_message 内）。"""
    render_metrics(data)
    st.markdown("---")
    render_engagement_chart(data, element_id=element_id)
    st.markdown("---")
    render_language_github_bar(data, element_id=element_id)


def render_ai_summary(summary: str) -> None:
    """AI 评级：独立区块 + Markdown 小标题，避免与图表同处 chat 容器被裁切。"""
    st.markdown(
        '<div class="ai-rating-outer"><p class="ai-rating-title">✨ AI 评级解读</p></div>',
        unsafe_allow_html=True,
    )
    st.markdown(summary)


def render_ai_summary_slot(*, ai_enabled: bool, ai_summary: str | None) -> None:
    """在 chat_message 之外渲染 AI 区块。"""
    if not ai_enabled:
        return
    st.markdown("---")
    if ai_summary:
        render_ai_summary(ai_summary)
    else:
        st.info("未生成 AI 解读内容。")


def render_user_bubble(content: str) -> None:
    """用户 URL：右侧圆角气泡，无头像。"""
    display = _repo_title_text(content) or content.strip()
    display = _ellipsis(display, 56)
    safe = (
        display.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )
    st.markdown(
        f'<div class="user-msg-row"><div class="user-msg-bubble">{safe}</div></div>',
        unsafe_allow_html=True,
    )


def render_message(msg: dict, index: int) -> None:
    role = msg["role"]
    if role == "user":
        render_user_bubble(msg["content"])
        return

    element_id = msg.get("id") or f"msg_{index}"

    with st.chat_message("assistant", avatar=None):
        if msg.get("error"):
            st.error(msg["error"])
        elif msg.get("result"):
            render_repo_metrics_and_charts(msg["result"], element_id=element_id)

    if msg.get("result"):
        render_ai_summary_slot(
            ai_enabled=bool(msg.get("ai_enabled")),
            ai_summary=msg.get("ai_summary"),
        )


def process_repo_query(prompt: str) -> None:
    """处理一次仓库查询（首屏提交或底部 chat_input 共用）。"""
    prompt = prompt.strip()
    if not prompt:
        return

    chat_id = ensure_chat_for_input()
    append_user_message(chat_id, prompt)

    url_key = _url_cache_key(prompt)
    token = _github_token()
    ai_on = bool(st.session_state.ai_analysis_enabled)
    api_key = _deepseek_api_key()

    if not url_key:
        append_assistant_message(
            chat_id,
            error="无法解析仓库地址，请使用形如 https://github.com/owner/repo 的链接",
        )
        st.rerun()
        return

    track_id = _cache_track_id(url_key, token, ai_on, api_key)
    from_cache = _is_analysis_cache_hit(track_id)
    spinner_msg = (
        "检测到相同 URL，读取缓存中…"
        if from_cache
        else "正在获取仓库数据…"
    )

    with st.spinner(spinner_msg):
        bundle = _cached_load_analysis(url_key, token, ai_on, api_key)
        _mark_analysis_cache_hit(track_id)

    outcome = _bundle_to_outcome(bundle)

    if outcome.kind == "ok" and outcome.result:
        append_assistant_message(
            chat_id,
            result=outcome.result,
            ai_enabled=bool(bundle.get("ai_enabled")),
            ai_summary=bundle.get("ai_summary"),
        )
    else:
        append_assistant_message(chat_id, error=outcome.error or "分析失败")

    st.rerun()


def render_sidebar() -> None:
    with st.sidebar:
        st.markdown('<div class="sidebar-brand">🔍 GitHub 仓库体检</div>', unsafe_allow_html=True)

        if st.button(
            "＋ 新仓库",
            width="stretch",
            type="secondary",
            key="btn_new_repo",
        ):
            start_new_workspace()
            st.rerun()

        st.markdown(
            '<div class="sidebar-ai-toggle"></div>',
            unsafe_allow_html=True,
        )
        st.toggle(
            "✨ AI 分析",
            key="ai_analysis_enabled",
            help="默认开启：调用 DeepSeek 生成仓库解读；关闭后仅展示 GitHub 基础指标",
        )

        st.markdown('<p class="history-label">历史会话</p>', unsafe_allow_html=True)

        active_id = st.session_state.active_chat_id
        sessions = st.session_state.chat_sessions
        history_ids = visible_chat_ids()

        if not history_ids:
            st.caption("暂无历史，分析后将显示在此")
        else:
            for chat_id in history_ids:
                _render_sidebar_session_item(
                    chat_id, sessions, active_id
                )

        st.markdown("---")
        st.caption("")


def _render_sidebar_session_item(
    chat_id: str, sessions: dict, active_id: str | None
) -> None:
    meta = sessions[chat_id]
    raw_title = meta["title"] or "未命名仓库"
    full_title = _repo_title_text(raw_title) or raw_title
    is_active = chat_id == active_id
    active_cls = " active" if is_active else ""
    st.markdown(
        f'<div class="session-item-anchor{active_cls}"></div>',
        unsafe_allow_html=True,
    )

    col_title, col_del = st.columns([12, 1], gap="small", vertical_alignment="center")
    with col_title:
        if st.button(
            _sidebar_button_label(full_title, active=is_active),
            key=f"open_{chat_id}",
            width="stretch",
            type="secondary",
            help=full_title,
        ):
            if chat_id != active_id:
                st.session_state.active_chat_id = chat_id
                st.rerun()
    with col_del:
        if st.button("×", key=f"del_{chat_id}", help=f"删除：{full_title}"):
            delete_chat(chat_id)
            st.rerun()


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------
render_sidebar()

# ---------------------------------------------------------------------------
# 主会话区
# ---------------------------------------------------------------------------
messages = get_active_messages()
_has_messages = bool(messages)

# 控制首屏 / 会话底栏样式（配合 CSS 显示或隐藏 chat_input）
_body_mode = "chat-active" if _has_messages else "welcome-mode"
st.markdown(
    f"<script>document.body.classList.remove('welcome-mode','chat-active');"
    f"document.body.classList.add('{_body_mode}');</script>",
    unsafe_allow_html=True,
)

if not messages:
    st.markdown(
        """
        <div class="chat-welcome">
            <h1>🔍 GitHub 仓库体检</h1>
            <p>在下方输入公开仓库链接；可在左侧栏切换 <b>✨ AI 分析</b>（默认开启）</p>
        </div>
        """,
        unsafe_allow_html=True,
    )

if messages:
    st.markdown('<div class="chat-thread">', unsafe_allow_html=True)
    for i, msg in enumerate(messages):
        render_message(msg, i)
    st.markdown("</div>", unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# 输入区：首屏居中输入；有会话后底部 chat_input（AI 开关始终在侧边栏）
# ---------------------------------------------------------------------------
if not _has_messages:
    st.markdown('<div class="center-search-anchor"></div>', unsafe_allow_html=True)
    _c_in, _c_go = st.columns([10, 1], gap="small")
    with _c_in:
        _welcome_url = st.text_input(
            "GitHub 仓库 URL",
            placeholder="https://github.com/owner/repo",
            label_visibility="collapsed",
            key="welcome_url_input",
        )
    with _c_go:
        _welcome_submit = st.button("→", help="开始体检", width="stretch")

    if _welcome_submit and _welcome_url and _welcome_url.strip():
        process_repo_query(_welcome_url)
else:
    if prompt := st.chat_input(
        "输入 GitHub 仓库 URL，例如 https://github.com/owner/repo",
        key="repo_chat_input",
    ):
        process_repo_query(prompt)
