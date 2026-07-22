# GYMTI 使用单一版本化 JSON 合同

状态：已接受（2026-07-23）

现有问卷原型的题目、选项与计分内容转换为单一版本化 JSON 合同，Vue 和现有 Python FastAPI 从同一事实源加载并进行 schema 校验；Vue 负责界面、Dexie 状态和本地降级，FastAPI 负责无状态复算、校验及 LLM 调用。独立 CommonJS 服务、服务端内存会话、原型静态页面和 iframe 不进入正式架构，运行时也不解析 Markdown。我们接受 TypeScript 与 Python 各有一个合同解释器的维护成本，并用共享黄金测试向量锁定得分、排除、提前完成、次要倾向、终局候选和正式结果一致性，以换取与现有 Vue/FastAPI 架构的直接集成和单一规则事实源。
