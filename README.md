# 🔍 ArXiv 论文检索 Agent

基于 DeepSeek 大模型 API 驱动的智能 arXiv 论文检索代理。

## 功能特性

- 🗣️ **自然语言检索**：用中文描述检索需求，Agent 自动理解并构造检索式
- 🔄 **智能迭代优化**：自动审核检索结果，不满意时迭代优化检索策略
- 📡 **实时流式展示**：Agent 思考过程、检索状态实时流式展示
- 🧠 **检索记忆**：记住历史检索轮次，避免重复无效检索
- 📤 **多格式导出**：支持 Markdown、CSV、JSON 格式导出对话和结果
- 🎨 **精美 UI**：基于 Gradio 的现代化交互界面

## 项目结构

```
ArxivAgent/
├── prompts/             # 提示词模板（纯文本，中文）
│   ├── system.txt       # 系统提示词
│   ├── query_parse.txt  # 需求理解 & 构造检索式
│   ├── result_review.txt# 审核检索结果
│   ├── refine_query.txt # 优化检索策略
│   └── summary.txt      # 生成最终报告
├── core/                # 核心模块
│   ├── llm.py           # DeepSeek API 封装
│   ├── arxiv_search.py  # arXiv API 封装
│   ├── memory.py        # 对话 & 检索记忆
│   ├── agent.py         # Agent 主循环
│   └── exporter.py      # 导出功能
├── app.py               # Gradio UI 入口
├── config.py            # 配置管理
└── requirements.txt     # 依赖
```

## Agent 工作流程

```
用户输入 → [理解需求] → 构造检索式 → [arXiv 检索] → [审核结果]
                                                       ↓
                                           满意？→ 否 → [优化策略] → 重新检索
                                                       ↓
                                                       是 → [生成报告] → 完成
```

## 快速开始

```bash
# 1. 创建虚拟环境
python -m venv .venv
.venv\Scripts\activate       # Windows
# source .venv/bin/activate  # Linux/Mac

# 2. 安装依赖
pip install -r requirements.txt

# 3. 启动
python app.py
```

访问 `http://localhost:7860` 使用。

## 配置

编辑 `config.py` 可修改：
- DeepSeek API Key 和模型配置
- arXiv 检索参数
- 最大检索轮次
