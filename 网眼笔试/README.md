# GitHub 仓库体检

基于 Streamlit 的公开 GitHub 仓库「体检」工具：输入仓库链接即可查看 Stars/Forks、语言分布、社区互动图表；可选接入 **DeepSeek** 对 README 与指标做分级解读（夯 / 顶级 / 人上人 / NPC / 拉完了）。

## 项目做什么

1. **拉取仓库数据**（PyGithub）  
   解析 `https://github.com/owner/repo` 或 `owner/repo`，获取 Stars、Forks、Watchers、Issues、许可证、Topics、语言占比、README 等。

2. **可视化展示**  
   - 基础指标卡片  
   - 社区互动柱状图  
   - 与 GitHub 网页一致的 Languages 堆叠条（Linguist 字节统计）

3. **AI 评级解读**（可选，默认开启）  
   调用 DeepSeek 前会先对 README 做**预处理**（`readme_processor.py`），再与 GitHub 指标一并送入模型：
   - **剔除图片/媒体占位**：README 在仓库里以 Markdown/HTML **标识**存在（如 `![alt](url)`、`<img src="...">`），并非真实像素内容；其中的 `src`、扩展名链接等对大模型几乎无语义价值，却会占用大量 token。
   - **清理范围**：Markdown 图片语法、HTML `<img>` / `<picture>` / `<video>`、指向 png/jpg/gif/svg 等的独立图片链接行、HTML 注释等；保留正文文字与结构。
   - **长度控制**：净化后的文本超过约 12,000 字符时会截断并标注，避免上下文过长。
   - **模型侧**：使用 DeepSeek Chat API，结合「净化后的 README + 量化指标」输出分节评级（夯 / 顶级 / 人上人 / NPC / 拉完了）；指标平淡时更细读 README 可学习点，README 过短或缺失则如实说明。
   - **缓存**：同一仓库重复查询走 **`st.cache_data` 缓存**，不再重复调用 GitHub / DeepSeek。

4. **会话式界面**  
   - 类聊天布局：用户 URL 气泡 + 助手侧结果  
   - 侧边栏：新仓库、历史会话、AI 开关  

## 技术栈

| 类别 | 技术 |
|------|------|
| 前端 / 应用框架 | [Streamlit](https://streamlit.io/) |
| GitHub 数据 | [PyGithub](https://pygithub.readthedocs.io/) |
| 大模型 | [DeepSeek API](https://platform.deepseek.com/)（OpenAI SDK 兼容调用） |
| 图表 | [Plotly](https://plotly.com/python/) + [Pandas](https://pandas.pydata.org/) |
| 语言 | Python 3.10+ |

## 项目结构

```
.
├── app.py                 # 主界面、会话、缓存、图表渲染
├── github_analyzer.py     # 仓库 URL 解析与指标拉取
├── ai_analyzer.py         # DeepSeek 提示词与评级调用
├── readme_processor.py    # README 预处理：剔除图片/媒体标识与无意义 src，截断后供 DeepSeek
├── requirements.txt
└── .streamlit/
    └── secrets.toml       # 本地密钥（勿提交 Git）
```

## 环境准备

- 已安装 **Python 3.10+**
- 可选：**GitHub Personal Access Token**（提高 API 限额，见 [GitHub Settings → Tokens](https://github.com/settings/tokens)）
- 可选：**DeepSeek API Key**（开启 AI 分析时需要，见 [DeepSeek 开放平台](https://platform.deepseek.com/)）

### 配置密钥

在项目根目录创建 `.streamlit/secrets.toml`：

```toml
# 可选：提高 GitHub API 限额
GITHUB_TOKEN = "ghp_xxxxxxxx"

# 可选：AI 分析
DEEPSEEK_API_KEY = "sk-xxxxxxxx"
```

也可使用环境变量：`GITHUB_TOKEN`、`DEEPSEEK_API_KEY`。

> 未配置 `DEEPSEEK_API_KEY` 时仍可查看 GitHub 指标；未配置 `GITHUB_TOKEN` 时公开仓库通常可用，但易触发频率限制。

## 一键启动

在 **PowerShell** 中进入项目根目录，任选一种方式：

**方式 A（推荐）**

```powershell
.\start.ps1
```

**方式 B（单条命令）**

```powershell
if (-not (Test-Path .\venv)) { python -m venv venv }; .\venv\Scripts\pip install -r requirements.txt -q; .\venv\Scripts\streamlit run app.py
```

浏览器将打开本地地址（一般为 `http://localhost:8501`）。在页面输入公开仓库 URL 即可开始体检。

### 分步启动（与上一键命令等价）

```powershell
cd "项目根目录路径"
python -m venv venv
.\venv\Scripts\pip install -r requirements.txt
.\venv\Scripts\streamlit run app.py
```

## 使用说明

1. 首屏或底部输入框填入公开仓库链接，例如 `https://github.com/owner/repo`。  
2. 左侧栏 **✨ AI 分析** 可开关 DeepSeek 解读。  
3. **再次查询同一仓库**（且 AI 开关、密钥配置未变）时，会提示「检测到相同 URL，读取缓存中…」，直接读缓存。  
4. 侧边栏 **＋ 新仓库** 可开始新会话；历史会话按 `owner/repo` 标题保存。

## 常见问题

- **GitHub 频率限制**：在 `secrets.toml` 中配置 `GITHUB_TOKEN`。  
- **AI 分析失败**：检查 `DEEPSEEK_API_KEY` 与网络；重启 Streamlit 使配置生效。  
- **缓存未更新**：修改代码或重启应用会清空 `st.cache_data`；同一 URL 在单次运行期间复用缓存。

## 许可证

本项目为笔试/演示用途；使用 GitHub、DeepSeek 等服务时请遵守各自服务条款。
