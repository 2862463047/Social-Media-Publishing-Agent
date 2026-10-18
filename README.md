# Social-Media-Publishing-Agent
基于 LangGraph 的多平台社交媒体内容生成、自审、修订与发布工作流。输入一个"想法"（idea）和目标平台列表，Agent 会自动为每个平台撰写符合平台调性的文案，经过「确定性长度校验 + LLM 评审」双重审查，未通过时自动修订（最多 MAX_ITERS 轮），最终通过 Publora API 创建草稿或定时发布。
