"""
OpenAI-compatible LLM API 封装模块
支持流式和非流式调用，以及提示词模板加载
"""
import json
import os
import re
import threading
from typing import Optional

from openai import OpenAI

import config


class CancelledError(Exception):
    """用户请求取消检索时抛出。被 agent 顶层 try 捕获后转 cancelled 事件。"""


def _is_cancelled(cancel_event: Optional[threading.Event]) -> bool:
    return cancel_event is not None and cancel_event.is_set()


def load_prompt(template_name: str, **kwargs) -> str:
    """加载提示词模板并填充变量，避免 str.format 大括号冲突"""
    filepath = os.path.join(config.PROMPTS_DIR, template_name)
    with open(filepath, "r", encoding="utf-8") as f:
        template = f.read()
    for k, v in kwargs.items():
        template = template.replace(f"{{{k}}}", str(v))
    return template


def get_client(api_key: str = None, base_url: str = None) -> OpenAI:
    """获取 OpenAI 兼容客户端"""
    return OpenAI(
        api_key=api_key or config.DEEPSEEK_API_KEY,
        base_url=base_url or config.DEEPSEEK_BASE_URL,
    )


def _get_field(obj, name: str, default=None):
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _stream_chunk_content(chunk) -> Optional[str]:
    choices = _get_field(chunk, "choices") or []
    if not choices:
        return None

    choice = choices[0]
    delta = _get_field(choice, "delta")
    if delta is None:
        return None

    content = _get_field(delta, "content")
    if content is None:
        return None
    if isinstance(content, str):
        return content
    return str(content)


def chat(messages: list, api_key: str = None, base_url: str = None, model: str = None) -> str:
    """非流式调用模型 API，返回完整文本（支持超时和重试）"""
    client = get_client(api_key, base_url)
    for attempt in range(3):
        try:
            response = client.chat.completions.create(
                model=model or config.DEEPSEEK_MODEL,
                messages=messages,
                temperature=0.3,
                stream=False,
                timeout=60.0,
            )
            return response.choices[0].message.content
        except Exception as e:
            if attempt == 2:
                raise RuntimeError(f"模型 API 调用失败（已重试3次）: {e}")
            import time
            time.sleep(1.0 * (attempt + 1))


def stream_chat(messages: list, api_key: str = None, base_url: str = None, model: str = None,
                cancel_event: Optional[threading.Event] = None):
    """
    流式调用模型 API，逐 token yield（支持超时和捕获异常）
    yields: str (每个 token)

    若传入 cancel_event 且在流过程中被 set，则关闭流并抛出 CancelledError，
    让上层 agent 把运行转成 cancelled 事件而不是等到 timeout。
    """
    # 发请求前先检查一次（避免发起昂贵调用）
    if _is_cancelled(cancel_event):
        raise CancelledError("cancelled before stream start")

    client = get_client(api_key, base_url)
    response = None
    try:
        response = client.chat.completions.create(
            model=model or config.DEEPSEEK_MODEL,
            messages=messages,
            temperature=0.3,
            stream=True,
            timeout=60.0,
        )
        for chunk in response:
            # 每个 token 边界检查取消标志：秒级响应取消
            if _is_cancelled(cancel_event):
                raise CancelledError("cancelled during stream")
            content = _stream_chunk_content(chunk)
            if content is not None:
                yield content
    except CancelledError:
        raise
    except Exception as e:
        raise RuntimeError(f"模型 API 流式调用出错: {e}")
    finally:
        # 主动关闭底层 HTTP 流，释放连接
        if response is not None:
            try:
                response.close()
            except Exception:
                pass


def build_messages(system_prompt: str, user_content: str, history: list = None) -> list:
    """构建消息列表"""
    messages = [{"role": "system", "content": system_prompt}]
    if history:
        messages.extend(history)
    messages.append({"role": "user", "content": user_content})
    return messages


def parse_json_response(text: str) -> dict:
    """
    从 LLM 输出中提取 JSON。
    处理可能包含 markdown 代码块的情况。
    """
    # 尝试提取 ```json ... ``` 代码块
    json_match = re.search(r'```(?:json)?\s*\n?(.*?)\n?\s*```', text, re.DOTALL)
    if json_match:
        text = json_match.group(1)
    
    # 尝试直接找到 JSON 对象
    text = text.strip()
    
    # 找到第一个 { 和最后一个 }
    start = text.find('{')
    end = text.rfind('}')
    if start != -1 and end != -1:
        text = text[start:end + 1]
    
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        # 尝试修复常见 JSON 格式问题
        # 去除可能的注释
        cleaned = re.sub(r'//.*?\n', '\n', text)
        cleaned = re.sub(r'/\*.*?\*/', '', cleaned, flags=re.DOTALL)
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            raise ValueError(f"无法解析 LLM 输出为 JSON: {e}\n原始文本: {text[:500]}")
