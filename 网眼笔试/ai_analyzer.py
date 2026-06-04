"""调用 DeepSeek 对仓库指标与 README 文本进行 AI 解读。"""



from __future__ import annotations



from openai import APIConnectionError, APIStatusError, OpenAI



from github_analyzer import RepoAnalysis

from readme_processor import truncate_for_llm



DEEPSEEK_BASE_URL = "https://api.deepseek.com"

# 官方别名，等价于 v4-flash 非思考模式，兼容性更好

DEEPSEEK_MODEL = "deepseek-chat"

README_MAX_CHARS = 12000

AI_MAX_OUTPUT_TOKENS = 2048

THINKING_DISABLED = {"thinking": {"type": "disabled"}}





def _format_languages(languages: dict[str, int]) -> str:

    if not languages:

        return "无"

    total = sum(languages.values()) or 1

    parts = []

    for lang, nbytes in sorted(languages.items(), key=lambda x: x[1], reverse=True)[:12]:

        pct = nbytes / total * 100

        parts.append(f"{lang} {pct:.1f}%")

    return "、".join(parts)





def build_metrics_context(repo: RepoAnalysis, readme_plain: str) -> str:

    readme_section = readme_plain.strip() or "（无 README 或内容为空）"

    readme_section = truncate_for_llm(readme_section, README_MAX_CHARS)



    topics = "、".join(repo.topics[:10]) if repo.topics else "无"

    return f"""## 仓库基础指标

- 仓库：{repo.full_name}

- 描述：{repo.description or "无"}

- Stars：{repo.stars:,} | Forks：{repo.forks:,} | Watchers：{repo.watchers:,} | Open Issues：{repo.open_issues:,}

- 体积：{repo.size_kb:,} KB | 默认分支：{repo.default_branch}

- 许可证：{repo.license_name or "未声明"} | 可见性：{repo.visibility}

- 是否 Fork：{repo.is_fork} | 是否归档：{repo.archived}

- Topics：{topics}

- 语言分布：{_format_languages(repo.languages)}

- 创建/更新/最近推送：{repo.created_at} / {repo.updated_at} / {repo.pushed_at or "—"}



## README 正文（已预先剔除图片，仅保留文字）

{readme_section}

"""





SYSTEM_PROMPT = """你是一位专业、有温度的开源项目分析师。根据 GitHub 量化指标与 README 纯文本（已去图），用中文输出「先评级、再分节分析」。

## 评审原则
- 综合 README 与指标作出判断；语气专业、坦诚，避免冷漠挑刺或唯指标论。Stars、Forks、许可证、体积等**仅作辅助参考**，勿堆砌数据、勿用指标替代对项目的实质理解。
- **指标较亮眼时**：可结合社区热度、维护情况等给出评价，README 作补充说明。
- **指标平淡或偏低时**：不要仅凭 Star 少就贬低；**应更认真细读 README**，看是否藏有可进一步学习的细节（如 Agent/工程实践、踩坑总结、学习笔记等）。若 README 确有深度与参考价值，应据此上调评级——小众个人仓库同样可以有意义，不要求作者有业界名望或高关注度。
- **README 极短、空洞或缺失时**：没有可深挖的文本依据，应如实说明「内容不足以支撑深入评价」，评级相应保守，勿编造、勿硬拔高。
- Star 多不等于夯；Star 少也不等于拉完了，关键看指标与 README 是否相互印证。不编造 README 中不存在的内容（图片已移除，勿提「见图」）。

## 评级档位（从高到低，只能选其一）
仅评级结论使用下列网络流行的五级称谓；分析正文不得玩梗、抖机灵或使用网络烂梗：
夯 → 顶级 → 人上人 → NPC → 拉完了

## 输出结构（必须遵守）
1. 开头单独一段，固定句式：「这个 GitHub 仓库给到一个【档位名】，我来解释一下给到【档位名】的原因。」
2. 随后用 3～5 个小标题分节（Markdown 三级标题 `### 标题名`），每节 1～3 句话，紧扣标题。
3. 小标题自拟、贴合仓库；可据实际情况组合，如：项目与定位、README 可读价值、可学习之处、维护与社区（辅助）、综合判断等。
4. 全文须写完整、有收束；不规定字数，简练不灌水。
5. 允许 `###` 与段落；不要用一级/二级标题、列表、加粗斜体、代码块或 HTML。"""





def analyze_repo_with_ai(

    repo: RepoAnalysis,

    readme_plain: str,

    *,

    api_key: str,

) -> str:

    client = OpenAI(api_key=api_key, base_url=DEEPSEEK_BASE_URL)

    user_content = build_metrics_context(repo, readme_plain)



    try:

        response = client.chat.completions.create(

            model=DEEPSEEK_MODEL,

            messages=[

                {"role": "system", "content": SYSTEM_PROMPT},

                {"role": "user", "content": user_content},

            ],

            temperature=0.5,

            max_tokens=AI_MAX_OUTPUT_TOKENS,

            extra_body=THINKING_DISABLED,

        )

    except APIConnectionError as e:

        raise RuntimeError(f"无法连接 DeepSeek API：{e}") from e

    except APIStatusError as e:

        raise RuntimeError(f"DeepSeek API 错误：{e.message}") from e



    choice = response.choices[0]

    content = (choice.message.content or "").strip()

    if not content:

        raise RuntimeError("DeepSeek 返回内容为空")



    if choice.finish_reason == "length":

        content += "\n\n（注：输出触及长度上限，内容可能不完整。）"



    return content


