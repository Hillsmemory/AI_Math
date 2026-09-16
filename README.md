# NLP-to-Lean4 辅助证明助手

基于 LLM + Agent 反馈闭环的自然语言辅助证明器：把中文数学命题转换为 Lean4 形式化证明代码。

## 快速开始

### 一键启动
```bash
# Windows: 双击 start.bat
# 或手动执行
streamlit run app.py
```
浏览器自动打开 `http://localhost:8501`。

### 依赖安装
```bash
pip install -r requirements.txt
```

## 使用方式

### 1. 演示模式（无需任何 Key）
直接点左侧栏的「演示：加法交换律」或「演示：偶数平方」按钮，即可看到完整的 Agent 反馈闭环流程（含 2 轮迭代、编译错误、修正过程）。

### 2. 真实模式（需 API Key）
1. 在 `.env` 填入你的 API Key（参考 `.env.example`）
2. 左侧栏选择 LLM 模型（支持 OpenAI / DeepSeek / Qwen）
3. 输入数学命题，点击「开始生成」

支持的模型：
- OpenAI: `gpt-4o`, `gpt-4o-mini`
- DeepSeek: `deepseek-chat`
- Qwen: `qwen-plus`, `qwen-max`

## 文件结构

| 文件 | 阶段 | 说明 |
|---|---|---|
| `compiler.py` | 阶段1 | Lean4 编译器封装（真实 / 模拟双模式）|
| `agent.py` | 阶段2 | ProofAgent 反馈闭环 |
| `app.py` | 阶段3 | Streamlit 主界面 |
| `prompts.py` | 阶段4 | System Prompt + Few-shot 示例 |
| `demo_data.py` | - | 内置演示数据（含完整迭代序列）|
| `llm_client.py` | - | LLM API 客户端封装 |

## 反馈闭环工作流程

```
用户输入「证明：加法交换律」
        ↓
LLM 生成首轮 Lean4 代码
        ↓
Lean4 编译器检查 ───── 通过 ──→ 输出最终代码
        ↓ 失败
提取错误信息 + 中文解释
        ↓
反馈给 LLM：「根据这些错误修正代码」
        ↓
LLM 输出修正后的代码
        ↓
（重复直到通过或达到 5 轮上限）
```

## Lean4 安装（可选）

未安装 Lean4 时自动启用静态语法检查（模拟模式）。如需真实编译：

### Windows
```powershell
# 安装 elan（Lean4 版本管理器）
Invoke-WebRequest -Uri "https://raw.githubusercontent.com/leanprover/elan/master/elan-init.ps1" -OutFile elan-init.ps1
.\\elan-init.ps1
```

### 验证
```bash
lean --version
```

## 实验背景

山东大学《人工智能应用实践》课程项目——基于开源大模型的应用系统创新。验证 Agent 反馈闭环架构在形式化证明任务上的可行性。
