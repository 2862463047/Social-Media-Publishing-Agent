import os
import json
from typing import TypedDict, Optional
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import requests
from pydantic import BaseModel, Field
from langchain_openai import ChatOpenAI
from langgraph.graph import StateGraph, END

OPEN_API_KEY = os.environ.get("TONGYI_API_KEY")
PUBLORA_API_KEY = os.environ.get("PUBLORA_API_KEY")
PUBLORA_BASE = "https://api.publora.com/api/v1"

DRY_RUN = True   # 真 -> 创建草稿（或打印）。假 -> 在schedule_time实际安排时间。
MAX_ITERS = 2    # 在发布我们的内容之前，最多允许进行多少次自我修改？

# 各大平台的内容审核规则
PLATFORM_SPECS = {
    "twitter": {"max_chars": 280,
                "style": "punchy and concrete; a strong hook in the first line; at most 1-2 hashtags"},
    "linkedin": {"max_chars": 3000,
                 "style": "professional and value-driven; short paragraphs; one clear takeaway; no hashtag spam"},
    "bluesky": {"max_chars": 300, "style": "conversational and authentic; minimal hashtags; a builder-to-builder tone"},
    "threads": {"max_chars": 500, "style": "casual and engaging; invites replies; light, human voice"},
    "mastodon": {"max_chars": 500, "style": "informative and community-friendly; plain, non-salesy"},
}

# Publora客户端
# 我们在所需的两个 REST 接口上添加了一层简单的封装。
# 认证信息通过一个头部字段 x-publora-key 来传递；这些密钥永远不会过期。
# 使用 scheduledTime 可以指定发送请求的时间，如果不指定，则该请求仅会以草稿形式存在。
def publora_request(method: str, path: str, body: dict | None = None, idempotency_key: str | None = None) -> dict:
    """经过身份验证的调用，直接接入Publora REST API。"""
    headers = {"x-publora-key": PUBLORA_API_KEY}
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key
    resp = requests.request(method, f"{PUBLORA_BASE}/{path}", headers=headers, json=body, timeout=30)
    if resp.status_code == 401:
        raise RuntimeError("无效的PUBLIRA_API_KEY——请在publora.com上生成一个新的密钥后再使用。")
    resp.raise_for_status()
    return resp.json()


def get_connections() -> list:
    """返回与该密钥关联的社交账户列表。"""
    return publora_request("GET", "platform-connections")["connections"]


def create_post(content: str, platform_id: str, scheduled_time: str | None = None) -> dict:
    """安排一个帖子发布。每次通话只能使用一个平台ID，这样就能为每个通话提供定制化的内容了."""
    body = {"content": content, "platforms": [platform_id]}
    if scheduled_time:
        body["scheduledTime"] = scheduled_time
    return publora_request("POST", "create-post", body=body, idempotency_key=str(uuid4()))

# 结构化输出
def get_llm():
    if not OPEN_API_KEY:
        raise RuntimeError("将OPENAI_API_KEY设置为用于生成内容的参数。")
    return ChatOpenAI(model="qwen3.8-flash", api_key=OPEN_API_KEY, base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",)


class PlatformPost(BaseModel):
    content: str = Field(description="这个平台上的待发布文章文本。")


class Critique(BaseModel):
    approved: bool = Field(description="如果帖子已经准备好被直接发布，那么就会为真。")
    note: str = Field(description="如果申请未被批准，会有一份简短且具体的说明，指导如何修复问题。")

# Agent状态
class AgentState(TypedDict):
    idea: str                       # 原始输入数据结构
    target_platforms: list          # 用户查询的平台列表
    connections: dict               # 平台与真实Publora平台ID的映射
    drafts: dict                    # 各个平台生成的草稿内容
    feedback: dict                  # 来自用户的反馈信息，包含修订指导
    iteration: int                  # 已经进行的评审轮数
    schedule_time: Optional[str]    # ISO 8601 UTC时间格式；仅在DRY_RUN为False时使用
    results: list                   # 每个平台的发布结果

"""
图节点：
1.fetch_connections — 将请求中的平台标识转换为实际的连接编号。如果没有 Publora 密钥，系统会使用模拟值，这样演示功能仍能正常运行。
2.generate — 为每个连接的平台编写（或重新编写）草稿。在修订阶段，它只会重新生成那些收到反馈的平台的相关内容。
3.review — 一个确定性长度门再加上一个 LLM 评价器。凡是表现不佳的模型都会产生反馈，以便进入下一轮训练。
4.路由规则——只要仍有反馈且数值未超过上限，就循环发送至 generate ；否则则直接发布。
5.publish — 将每份经过审核的草稿发送至 Publora。试运行版本（或未设置密钥的版本）绝不会被正式发布。
"""
def fetch_connections(state: AgentState) -> dict:
    targets = state["target_platforms"]
    if PUBLORA_API_KEY:
        mapping = {}
        for c in get_connections():
            prefix = c["platformId"].split("-", 1)[0]      # "twitter-123" -> "twitter"
            mapping.setdefault(prefix, c["platformId"])     # 每个平台的第一个账户
        connections = {p: mapping[p] for p in targets if p in mapping}
        missing = [p for p in targets if p not in mapping]
        if missing:
            print(f"在您的Publora账户中未连接该功能，将跳过此操作：{missing}")
    else:
        connections = {p: f"{p}-DEMO123" for p in targets}
        print("未设置PUBLORA_API_KEY——使用模拟连接进行测试运行。")
    print(f"Publishing to: {list(connections)}")
    return {"connections": connections}

def generate(state: AgentState) -> dict:
    llm = get_llm().with_structured_output(PlatformPost)
    drafts = dict(state.get("drafts") or {})
    feedback = state.get("feedback") or {}
    platforms = [p for p in state["target_platforms"] if p in state["connections"]]

    for p in platforms:
        if p not in PLATFORM_SPECS:
            print(f"对于{p}没有指定具体规格——将其添加到PLATFORM_SPECS文件中。跳过此项。")
            continue
        if p in drafts and p not in feedback:
            continue  # already approved, leave it
        spec = PLATFORM_SPECS[p]
        instruction = (
            f"You are a social media copywriter. Write a single {p} post about the idea below.\n"
            f"Idea: {state['idea']}\n\n"
            f"Constraints:\n"
            f"- Hard limit: {spec['max_chars']} characters.\n"
            f"- Voice: {spec['style']}.\n"
            f"- No preamble, no quotes around the post, return only the post text."
        )
        if p in feedback:
            instruction += f"\n\nRevise your previous draft. Reviewer feedback: {feedback[p]}\nPrevious draft: {drafts.get(p, '')}"
        result = llm.invoke(instruction)
        drafts[p] = result.content.strip()
    return {"drafts": drafts, "feedback": {}}


# 评估 - 一个确定性的长度门 + 一个LLM评价器。凡是表现不佳的模型都会产生反馈，以便进入下一轮训练。
def review(state: AgentState) -> dict:
    llm = get_llm().with_structured_output(Critique)
    feedback = {}
    for p, content in state["drafts"].items():
        spec = PLATFORM_SPECS[p]
        if len(content) > spec["max_chars"]:
            feedback[p] = f"Too long: {len(content)}/{spec['max_chars']} chars. Cut it down while keeping the hook."
            continue
        verdict = llm.invoke(
            f"Review this {p} post. Platform voice: {spec['style']}."
            f"Approve onli if it is obn-idea, on-voice, and publish ready.\n\nPost:\n{content}"
        )
        if not verdict.approved:
            feedback[p] = verdict.note

        kept = {p: state["drafts"][p] for p in state["drafts"] if p not in feedback}
        print(f"Review round {state.get('iteration', 0) + 1}: {len(kept)} approved, {len(feedback)} need revision.")
        return {"feedback": feedback, "iteration": state.get("iteration", 0) + 1}

# 路由规则：只要仍有反馈且数值未超过上限，就循环发送至 generate ；否则则直接发布。
def route_after_review(state: AgentState) -> str:
    if state["feedback"] and state["iteration"] < MAX_ITERS:
        return "generate"
    return "publish"


def publish(state: AgentState) -> dict:
    results = []
    for p, content in state["drafts"].items():
        platform_id = state["connections"][p]
        if not PUBLORA_API_KEY:
            print(f"[DRY-RUN] would POST create-post -> {platform_id}: {content[:60]}...")
            results.append({"platform": p, "status": "dry-run", "postGroupId": f"dry-{uuid4().hex[:8]}"})
            continue
        scheduled = None if DRY_RUN else state.get("schedule_time")
        try:
            resp = create_post(content, platform_id, scheduled_time=scheduled)
            results.append({
                "platform": p,
                "status": "scheduled" if scheduled else "draft",
                "postGroupId": resp.get("postGroupId"),
                "scheduledTime": resp.get("scheduledTime"),
            })
        except Exception as exc:  # 将某个平台的故障与其他平台区分开来
            print(f"Failed to publish to {p}: {exc}")
            results.append({"platform": p, "status": "error", "error": str(exc)})
    return {"results": results}

# 构建图
builder = StateGraph(AgentState)
builder.add_node("fetch_connections", fetch_connections)
builder.add_node("generate", generate)
builder.add_node("review", review)
builder.add_node("publish", publish)

builder.set_entry_point("fetch_connections")
builder.add_edge("fetch_connections", "generate")
builder.add_edge("generate", "review")
builder.add_conditional_edges("review", route_after_review, {"generate": "generate", "publish": "publish"})
builder.add_edge("publish", END)

agent = builder.compile()

# 测试
initial_state = {
    "idea": "We just shipped a REST + MCP API that lets AI agents publish to 10 social platforms with a single call.",
    "target_platforms": ["twitter", "linkedin", "bluesky"],
    "connections": {},
    "drafts": {},
    "feedback": {},
    "iteration": 0,
    "schedule_time": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
    "results": [],
}

final_state = agent.invoke(initial_state)

print("\n===== DRAFTS =====")
for platform, content in final_state["drafts"].items():
    print(f"\n--- {platform} ({len(content)} chars) ---\n{content}")

print("\n===== PUBLISH RESULTS =====")
print(json.dumps(final_state["results"], indent=2))

