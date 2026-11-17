"""Prompt Studio 接口：白名单文件读写，保存后下一次调用立即生效。"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

from backend.core import prompt_builder

router = APIRouter(prefix="/api/prompts", tags=["prompts"])


class PromptPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    content: str = Field(default="", max_length=20000)


@router.get("")
def list_prompts():
    return {"prompts": prompt_builder.list_prompts()}


@router.get("/{prompt_name}")
def get_prompt(prompt_name: str):
    return prompt_builder.get_prompt(prompt_name)


@router.put("/{prompt_name}")
def save_prompt(prompt_name: str, payload: PromptPayload):
    # 每次调用都重新读盘，所以这里只管原子写入，不需要清缓存或重启。
    prompt_builder.save_prompt(prompt_name, payload.content)
    return {"saved": prompt_name, "size": len(payload.content)}
