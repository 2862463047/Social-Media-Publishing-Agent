基于 LangGraph 的多平台社交媒体内容生成、自审、修订与发布工作流。输入一个"想法"（idea）和目标平台列表，Agent 会自动为每个平台撰写符合平台调性的文案，经过「确定性长度校验 + LLM 评审」双重审查，未通过时自动修订（最多 MAX_ITERS 轮），最终通过 Publora API 创建草稿或定时发布。
一、核心价值
痛点	本项目的解法
同一内容要在 Twitter / LinkedIn / Bluesky / Threads / Mastodon 等多个平台分别改写，工作量大	一次输入，多平台并行生成，每平台自动套用各自的字数上限与语气规范
各平台字数、语气、Hashtag 规则不同，人工容易踩坑	PLATFORM_SPECS 集中定义，生成与审查都以此为准
LLM 一次性输出质量不稳定	"生成 → 审查 → 修订"闭环，未达标自动重写，最多 2 轮兜底
自动发布风险高，怕发错内容	默认 DRY_RUN=True，只建草稿 / 打印；无 API Key 时自动降级为模拟连接，绝不误发
发布平台密钥管理分散	统一通过 PUBLORA_API_KEY 对接 Publora，一个 Key 管理全部社交账号
二、技术架构
2.1 技术栈
层	组件	作用
编排	LangGraph (StateGraph)	定义节点与条件路由，管理共享状态 AgentState
大模型	LangChain + ChatOpenAI 接入 通义千问（DashScope OpenAI 兼容模式）	内容生成 + 结构化评审
结构化输出	Pydantic (PlatformPost, Critique)	约束 LLM 输出格式，避免解析失败
发布通道	Publora REST API (requests)	platform-connections 查询连接、create-post 建草稿/定时发布
状态模型	TypedDict (AgentState)	全流程共享上下文
2.2 工作流拓扑
2.3 节点职责
节点	输入	输出	说明
fetch_connections	target_platforms	connections	调用 Publora 把 "twitter" 映射为 "twitter-123"；无 Key 时生成 *-DEMO123 模拟连接
generate	idea, drafts, feedback	drafts	只为新平台或收到反馈的平台生成，已通过的平台原样保留
review	drafts	feedback, iteration	先做确定性长度门（超限直接判负），再调用 LLM 评审语气与相关性
publish	drafts, connections	results	DRY_RUN=True 或 无 Key → 只建草稿/打印；DRY_RUN=False → 按 schedule_time 定时发布
2.4 共享状态 AgentState
python

class AgentState(TypedDict):
    idea: str                     # 原始想法
    target_platforms: list        # 目标平台列表
    connections: dict             # 平台 → Publora 平台ID
    drafts: dict                  # 平台 → 草稿文本
    feedback: dict                # 平台 → 修订意见
    iteration: int                # 已审查轮数
    schedule_time: Optional[str]  # ISO 8601 UTC 定时时间
    results: list                 # 每平台发布结果

三、程序功能说明
3.1 支持平台与规格

规格集中定义在 PLATFORM_SPECS 中，生成与审查共用同一份标准：
平台	字符上限	语气规范
twitter	280	简洁有力，首句强 Hook，最多 1–2 个 Hashtag
linkedin	3000	专业、价值驱动，短段落，一个明确结论，禁止堆砌 Hashtag
bluesky	300	对话感、真实，少 Hashtag，Builder-to-Builder 语气
threads	500	轻松、引发回复、有人情味
mastodon	500	信息性强、社区友好、平实非推销

    需要新增平台时，只要在 PLATFORM_SPECS 中补一条即可，Agent 会自动支持。

3.2 三种运行模式
模式	触发条件	行为
纯模拟（Dry-Run）	未设置 PUBLORA_API_KEY	使用 *-DEMO123 假连接，只在控制台打印即将发布的内容
建草稿	设置了 Key 且 DRY_RUN=True（默认）	调用 Publora 创建草稿，不指定 scheduledTime，人工确认后再发布
定时发布	设置了 Key 且 DRY_RUN=False	按 schedule_time（UTC ISO 8601）实际排期发布
3.3 自审闭环（Review Loop）

    确定性长度门：草稿字符数超过 max_chars → 直接产生反馈 Too long: 300/280 ...。

    LLM 评审器：调用 Critique 结构化输出，从 紧扣 idea、符合平台语气、可直接发布 三个维度判定。

    路由：若仍有 feedback 且 iteration < MAX_ITERS，回到 generate 定向重写未通过的平台；否则进入 publish。

    幂等：已通过的平台在下一轮 generate 中会被跳过（if p in drafts and p not in feedback: continue）。

3.4 发布结果结构

每个平台返回一条记录，便于排查是哪个平台失败：
json

[
  {"platform": "twitter", "status": "draft", "postGroupId": "grp_xxx", "scheduledTime": null},
  {"platform": "linkedin", "status": "dry-run", "postGroupId": "dry-1a2b3c4d"},
  {"platform": "bluesky", "status": "error", "error": "..."}
]

四、启动安装说明
4.1 环境要求

    Python ≥ 3.10（代码使用了 dict | None 联合类型语法）

    可访问阿里云 DashScope 接口与 Publora API 的网络环境

4.2 安装依赖
bash

# 建议使用虚拟环境
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate

pip install langgraph langchain-openai langchain-core pydantic requests

4.3 配置环境变量
变量	必填	说明
TONGYI_API_KEY	✅	阿里云百炼 / DashScope 的 API Key，用于调用通义千问
PUBLORA_API_KEY	⭕	Publora 平台密钥；不填则自动进入纯模拟模式
bash

# Windows CMD
set TONGYI_API_KEY=sk-xxxxxxxx
set PUBLORA_API_KEY=pk-xxxxxxxx

# Windows PowerShell
$env:TONGYI_API_KEY="sk-xxxxxxxx"
$env:PUBLORA_API_KEY="pk-xxxxxxxx"

# macOS / Linux
export TONGYI_API_KEY=sk-xxxxxxxx
export PUBLORA_API_KEY=pk-xxxxxxxx

4.4 运行
bash

python main.py

默认流程：生成 → 审查 → （必要时重写）→ 建草稿/打印 → 输出草稿与发布结果。
五、程序实践
5.1 输入示例
python

initial_state = {
    "idea": "We just shipped a REST + MCP API that lets AI agents publish to 10 social platforms with a single call.",
    "target_platforms": ["twitter", "linkedin", "bluesky"],
    "connections": {}, "drafts": {}, "feedback": {},
    "iteration": 0,
    "schedule_time": "2026-10-18T08:00:00+00:00",
    "results": [],
}
final_state = agent.invoke(initial_state)

5.2 输出示例
text

Publishing to: ['twitter', 'linkedin', 'bluesky']
Review round 1: 2 approved, 1 need revision.
Review round 2: 3 approved, 0 need revision.

===== DRAFTS =====
--- twitter (274 chars) ---
Ship social posts from your AI agent — one call, ten platforms.
Our new REST + MCP API handles auth, rate limits, and per-platform formatting for you. #AI #DevTools

--- linkedin (612 chars) ---
We just shipped something we've wanted for a long time...
[略]

--- bluesky (241 chars) ---
ok this is fun: our agent can now post to 10 platforms with a single REST or MCP call...

===== PUBLISH RESULTS =====
[
  {"platform": "twitter",  "status": "draft", "postGroupId": "grp_a1b2"},
  {"platform": "linkedin", "status": "draft", "postGroupId": "grp_c3d4"},
  {"platform": "bluesky",  "status": "draft", "postGroupId": "grp_e5f6"}
]

5.3 三种典型使用姿势

① 本地试跑（零配置体验）

不设置 PUBLORA_API_KEY，只设置 TONGYI_API_KEY，直接运行即可看到多平台草稿输出，不会触碰任何真实账号。

② 人工复核模式（推荐生产使用）

设置 PUBLORA_API_KEY，保持 DRY_RUN=True。Agent 只把内容建为 Publora 草稿，运营人员在 Publora 后台人工审阅后再点发布。

③ 全自动定时发布

设置 PUBLORA_API_KEY 且把 DRY_RUN=False，同时传入合法的 schedule_time（UTC ISO 8601），Agent 会直接排期发布。
5.4 参数调优
参数	位置	建议
MAX_ITERS	文件顶部	1–3；过大会增加 token 成本，过小可能放过不合格文案
DRY_RUN	文件顶部	生产建议保持 True，全自动发布前务必先跑几轮草稿
PLATFORM_SPECS	文件顶部	可自由增删平台与字符上限
schedule_time	initial_state	使用 datetime.now(timezone.utc) + timedelta(...) 生成
六、目录结构（单文件推荐拆分）
text

.
├── main.py                 # 当前单文件版本（README 对应代码）
├── .env                    # 环境变量（请勿提交到 Git）
├── requirements.txt
└── README.md

若要工程化，建议拆分为：
text

socialpilot/
├── config.py               # PLATFORM_SPECS、DRY_RUN、MAX_ITERS
├── publora.py              # publora_request / get_connections / create_post
├── llm.py                  # get_llm / PlatformPost / Critique
├── state.py                # AgentState
├── nodes/
│   ├── fetch_connections.py
│   ├── generate.py
│   ├── review.py
│   └── publish.py
└── graph.py                # StateGraph 装配与 compile

七、已知限制与使用提示

    review 节点缩进提醒：当前 kept 统计与 return 语句位于 for p, content in state["drafts"].items() 循环体内，因此每轮只会审查第一个平台就返回。若希望审查全部平台，应将这两行移出 for 循环。生产使用前建议先修正。

    模型名确认：代码中使用的 qwen3.8-flash 需与 DashScope 当前在售模型名一致（例如 qwen-flash / qwen-turbo / qwen-plus）。若运行时报模型不存在，请到阿里云百炼控制台核对。

    DRY_RUN 与 PUBLORA_API_KEY 是两级开关：即使 Key 已配置，DRY_RUN=True 时也只会建草稿，不会定时发布，这是刻意的安全设计。

    Token 消耗：每轮评审对每个平台发起一次 LLM 调用；MAX_ITERS=2 + 3 个平台时，单次运行约 9 次 LLM 调用。

    幂等性：create_post 为每次调用生成独立 Idempotency-Key（UUID4），可防止网络重试导致的重复发布；但重跑整个脚本会生成新的 Key，从而产生重复草稿，请通过 Publora 后台确认。

    失败隔离：单个平台发布失败不会影响其他平台，错误会以 {"status": "error", "error": "..."} 记录在 results 中。

八、可扩展方向

    🔌 接入更多平台：在 PLATFORM_SPECS 增加条目即可，Publora 侧只需预先连接对应账号。

    🖼️ 多模态内容：为 PlatformPost 增加 image_url 字段，create_post 透传媒体附件。

    🧠 A/B 多版本：generate 中为每个平台生成 2–3 个候选，review 阶段由 LLM 择优。

    📊 发布结果回收：新增 analytics 节点，定时拉取每条 postGroupId 的曝光/互动数据，反哺 review 的评审标准。

    🕒 批量定时：把 schedule_time 从单值扩展为 dict[platform, str]，实现错峰发布。

    🧪 单元测试：为 route_after_review、长度门、fetch_connections 的缺失平台分支补充测试用例。

九、License

按项目实际许可证补充（如 MIT / Apache-2.0）。