# GYMTI 日志不保留测评内容

状态：已接受（2026-07-23）

> 2026-07-23 取代注记：本文关于 `GYMTI_LLM_ENABLED=false`、`GYMTI_LLM_RETENTION_CONFIRMED=false` 双重默认关闭和评委会话门禁的部分已由 [ADR-0044](0044-gymti-uses-ark-doubao-minimal-data-and-independent-concurrency.md) 取代。日志字段白名单、无问卷内容留存和供应商训练／留存必须确认的边界继续有效。

GYMTI API 和 LLM 调用只记录请求 ID、合同版本、模型名、耗时、运行状态、所选下一题 ID 与错误类别，不记录请求正文、用户选项、得分、原因码、提示词、模型原文、结果叙事、训练档案、身份信息或自由用户画像；异常与调试日志同样经过字段白名单。模型供应商侧可配置的训练与内容留存必须关闭，不能满足该边界的配置不进入生产。工程配置以 `GYMTI_LLM_ENABLED=false` 和 `GYMTI_LLM_RETENTION_CONFIRMED=false` 双重默认关闭；只有显式确认供应商训练／留存配置符合本 ADR、并通过评委会话与预算门禁时才构造模型客户端，单独注入密钥不会启用调用。我们接受内容级线上排障能力降低，以确保无状态 API 不通过日志形成隐性问卷数据库，并以版本化合同、黄金向量和本地结果快照承担可复现与诊断责任。
