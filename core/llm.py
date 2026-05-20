"""
DeepSeek API 封装模块
支持流式和非流式调用，以及提示词模板加载
"""
import json
import os
import re
from openai import OpenAI

import config


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


def chat(messages: list, api_key: str = None, base_url: str = None, model: str = None) -> str:
    """非流式调用 DeepSeek API，返回完整文本（支持超时和重试）"""
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
                raise RuntimeError(f"DeepSeek API 调用失败（已重试3次）: {e}")
            import time
            time.sleep(1.0 * (attempt + 1))


def stream_chat(messages: list, api_key: str = None, base_url: str = None, model: str = None):
    """
    流式调用 DeepSeek API，逐 token yield（支持超时和捕获异常）
    yields: str (每个 token)
    """
    client = get_client(api_key, base_url)
    try:
        response = client.chat.completions.create(
            model=model or config.DEEPSEEK_MODEL,
            messages=messages,
            temperature=0.3,
            stream=True,
            timeout=60.0,
        )
        for chunk in response:
            if chunk.choices[0].delta.content is not None:
                yield chunk.choices[0].delta.content
    except Exception as e:
        raise RuntimeError(f"DeepSeek API 流式调用出错: {e}")


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
