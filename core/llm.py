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
    """加载提示词模板并填充变量"""
    filepath = os.path.join(config.PROMPTS_DIR, template_name)
    with open(filepath, "r", encoding="utf-8") as f:
        template = f.read()
    if kwargs:
        template = template.format(**kwargs)
    return template


def get_client(api_key: str = None) -> OpenAI:
    """获取 OpenAI 兼容客户端"""
    return OpenAI(
        api_key=api_key or config.DEEPSEEK_API_KEY,
        base_url=config.DEEPSEEK_BASE_URL,
    )


def chat(messages: list, api_key: str = None) -> str:
    """非流式调用 DeepSeek API，返回完整文本"""
    client = get_client(api_key)
    response = client.chat.completions.create(
        model=config.DEEPSEEK_MODEL,
        messages=messages,
        temperature=0.3,
        stream=False,
    )
    return response.choices[0].message.content


def stream_chat(messages: list, api_key: str = None):
    """
    流式调用 DeepSeek API，逐 token yield
    yields: str (每个 token)
    """
    client = get_client(api_key)
    response = client.chat.completions.create(
        model=config.DEEPSEEK_MODEL,
        messages=messages,
        temperature=0.3,
        stream=True,
    )
    for chunk in response:
        if chunk.choices[0].delta.content is not None:
            yield chunk.choices[0].delta.content


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
