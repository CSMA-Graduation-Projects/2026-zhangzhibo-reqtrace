# 大模型调用封装模块（系统调用 AI 的统一入口）
from __future__ import annotations

import json
import os
import re

from openai import OpenAI
from openai import AuthenticationError, RateLimitError, BadRequestError, APIConnectionError

from ai_service.core.config import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL


class LLMServiceError(Exception):
    pass

# 创建大模型客户端，带哦去AI配置
def _build_client() -> OpenAI:
    kwargs = {
        "api_key": LLM_API_KEY,
        "timeout": float(os.getenv("LLM_TIMEOUT", "8")),
    }
    if LLM_BASE_URL:
        kwargs["base_url"] = LLM_BASE_URL
    return OpenAI(**kwargs)

# 从模型返回内容里提取合法 JSON
def _extract_json(text: str) -> dict:
    text = (text or "").strip()

    # 1. 直接整体就是 JSON
    try:
        return json.loads(text)
    except Exception:
        pass

    # 2. 提取 ```json ... ``` 代码块
    code_block_match = re.search(r"```json\s*(.*?)\s*```", text, re.DOTALL | re.IGNORECASE)
    if code_block_match:
        block = code_block_match.group(1).strip()
        try:
            return json.loads(block)
        except Exception:
            pass

    # 3. 提取最外层 {...}
    obj_match = re.search(r"\{.*\}", text, re.DOTALL)
    if obj_match:
        obj_text = obj_match.group(0).strip()
        try:
            return json.loads(obj_text)
        except Exception:
            pass

    raise LLMServiceError(f"AI 调用失败：模型返回内容不是合法 JSON。原始返回：{text}")

# 调用大模型生成普通文本（字符串）
def chat_text(system_prompt: str, user_prompt: str, *, temperature: float = 0.2, max_tokens: int | None = None) -> str:
    if os.getenv("PYTEST_CURRENT_TEST"):
        raise LLMServiceError("pytest 环境下跳过真实 LLM 调用。")
    if not LLM_API_KEY:
        raise LLMServiceError("未检测到 LLM_API_KEY，请先检查 .env 配置。")

    client = _build_client()

    try:
        request_kwargs = {
            "model": LLM_MODEL,
            "temperature": float(temperature),
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        if max_tokens is not None:
            request_kwargs["max_tokens"] = int(max_tokens)

        resp = client.chat.completions.create(**request_kwargs)
        return resp.choices[0].message.content or ""

    except AuthenticationError:
        raise LLMServiceError("AI 调用失败：API Key 无效或未授权。")
    except RateLimitError as e:
        raise LLMServiceError(f"AI 调用失败：请求频率或额度受限。详情：{e}")
    except APIConnectionError:
        raise LLMServiceError("AI 调用失败：无法连接到模型服务，请检查网络或 LLM_BASE_URL。")
    except BadRequestError as e:
        raise LLMServiceError(f"AI 调用失败：请求参数错误。详情：{e}")
    except Exception as e:
        raise LLMServiceError(f"AI 调用失败：{e}")

# 调用大模型并要求返回 JSON（字典）
def chat_json(system_prompt: str, user_prompt: str, *, temperature: float = 0.2) -> dict:
    if os.getenv("PYTEST_CURRENT_TEST"):
        raise LLMServiceError("pytest 环境下跳过真实 LLM 调用。")
    if not LLM_API_KEY:
        raise LLMServiceError("未检测到 LLM_API_KEY，请先检查 .env 配置。")

    client = _build_client()

    try:
        resp = client.chat.completions.create(
            model=LLM_MODEL,
            temperature=float(temperature),
            messages=[
                {
                    "role": "system",
                    "content": system_prompt + "\n\n你必须只返回合法 JSON，不要输出解释，不要输出 markdown，不要输出代码块标记。"
                },
                {"role": "user", "content": user_prompt},
            ],
        )

        content = resp.choices[0].message.content or ""
        return _extract_json(content)

    except AuthenticationError:
        raise LLMServiceError("AI 调用失败：API Key 无效或未授权。")
    except RateLimitError as e:
        raise LLMServiceError(f"AI 调用失败：请求频率或额度受限。详情：{e}")
    except APIConnectionError:
        raise LLMServiceError("AI 调用失败：无法连接到模型服务，请检查网络或 LLM_BASE_URL。")
    except BadRequestError as e:
        raise LLMServiceError(f"AI 调用失败：请求参数错误。详情：{e}")
    except LLMServiceError:
        raise
    except Exception as e:
        raise LLMServiceError(f"AI 调用失败：{e}")