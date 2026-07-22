# 7 / 19 分钟长视频 A/B 验证

## 目的与边界

这是一项仅在本机进行的研究实验，用来为未来最多 30 分钟的完整视频动作分析挑选候选视觉路线。它不修改现有 60 秒产品 API、受控来源清单、COS/CDN、Caddy 或生产 Provider；现有 native-AV 63 单元基准也保持冻结。

实验输入仅限团队已授权的两条本地视频。原视频、派生音视频、联系表、转录、提示词、Provider 原始响应和逐事件结果都不得提交、上传到公开 COS 或写入产品来源清单。生产接入必须在目标服务器网络复跑同一协议并另行做架构决策。

## 冻结的两臂

| 对比臂 | 语音 | 视觉 | 融合 |
| --- | --- | --- | --- |
| A：Seed 联系表 | 完整豆包 ASR 2.0 | 每个 60 秒块的 Seed Mini 联系表 | Seed Mini |
| B：Qwen 静音视频 | 完整豆包 ASR 2.0 | 相同 60 秒静音块的 Qwen3-VL | Seed Mini |

两臂都不向模型提供动作清单；动作清单只用于实验结束后的离线评分。每条视频均按 60 秒分块、10 秒重叠处理，并把候选映射回原视频绝对时间，随后跨块去重和合并碎片。每个 arm/run 都独立执行完整 ASR，以保留真实端到端耗时；同一输入哈希的本地预处理材料可复用，并单独计时。

模型与协议版本固定为：

- ASR：`volc.seedasr.sauc.duration`
- Seed 视觉与融合：`doubao-seed-2-0-mini-260428`
- Qwen 视觉：`qwen3-vl-flash-2026-01-22`
- 分块：60 秒、10 秒重叠
- 提示词：冻结为 `long-video-ab-v1`，每张预检回执和原始结果均绑定其 SHA-256
- 可重试的网络、408、429 与 5xx：最多两次，并遵守 `Retry-After`

Qwen3-VL 仅参与静音视频视觉路线；本轮不会用 Qwen Omni 的既有 Schema 问题来阻塞它。不可恢复权限、模型、媒体格式和 Schema 错误不重试。无法安全验证幂等性的 multipart 上传不会被盲目重放。

## 清单、预检与并发校准

从 [long-video-experiment.example.json](../../benchmark/long-video-experiment.example.json) 复制一份到 Git 忽略的 `.benchmark-work/long-video/manifest.json`。示例中的外部路径和 64 位全零 SHA-256 是占位符：运行前必须替换为已授权原视频的实际路径、精确 SHA-256、实测时长，以及每条来源经人工复核的 `representative_start_seconds`，不能把占位清单直接运行。

所有真实调用必须显式通过 `socks5h://127.0.0.1:7897`。如环境解析出 `198.18.0.x` Fake-IP 但该代理不可用，预检应立即停止，不能回退为隐式直连。

预检按以下顺序进行；任一步在某 Provider 发生权限、模型、Schema、媒体格式或清理失败时，就停止该 Provider 的后续步骤：

1. 检查密钥存在性和精确模型 ID。
2. 执行不带媒体的最小调用以验证权益。
3. 使用 2 秒合成音视频验证上传、读取、结构化输出和清理。
4. 使用 8 秒真实片段验证真实媒体链路。
5. 使用一个 60 秒代表性块验证完整的分块链路。

预检会对两条来源各执行分级检查；预检通过后，从每 Provider 1 路并发开始，用混合来源代表块逐级校准到最多 3 路。出现不可恢复错误、Schema 错误、清理失败或持续限流时，回退到上一个安全值；主实验固定该并发值，不能运行中扩容。全局 Provider/model permit 加公平队列记录排队等待，且每个 arm 同时最多排入一个视觉块和一个融合块，避免 19 分钟来源预先塞满队列、污染 7 分钟对照。

## 主实验顺序

每条视频、每个对比臂运行 3 次，共 12 个完整 arm-run。两个 source job 在每个波次同步启动，且一个波次中的两个任务都结束后才能开始下一波：

| 重复 | 第一个同步波次 | 第二个同步波次 |
| --- | --- | --- |
| R1 | 7 分钟 A + 19 分钟 B | 7 分钟 B + 19 分钟 A |
| R2 | 7 分钟 B + 19 分钟 A | 7 分钟 A + 19 分钟 B |
| R3 | 7 分钟 A + 19 分钟 B | 7 分钟 B + 19 分钟 A |

实验目标是完整覆盖耗时不高于 15 秒 / 视频分钟：7 分钟约不高于 106 秒，19 分钟约不高于 290 秒。低于 5 秒 / 视频分钟是更好结果，不是失败条件。本机代理上传耗时只作诊断，不能视为生产 SLA。

未来 CLI 的固定调用契约如下：

```powershell
Copy-Item benchmark/long-video-experiment.example.json .benchmark-work/long-video/manifest.json
# 填写真实但不提交的路径、SHA-256 和时长后：
pnpm benchmark:long prepare --manifest .benchmark-work/long-video/manifest.json
pnpm benchmark:long gold-template --manifest .benchmark-work/long-video/manifest.json
# 完整播放并填写两份金标，将 reviewed 改为 true；run 会在真实调用前强制校验。
pnpm benchmark:long preflight --manifest .benchmark-work/long-video/manifest.json --real
pnpm benchmark:long calibrate --manifest .benchmark-work/long-video/manifest.json --real
pnpm benchmark:long run --manifest .benchmark-work/long-video/manifest.json --real
pnpm benchmark:long score --manifest .benchmark-work/long-video/manifest.json --output benchmark-results/long-video/summary.json
```

`--real` 是唯一允许调用真实 Provider 的模式。测试使用假 Provider，但非测试环境不得以假 Provider 替代真实结果。

ASR WebSocket、Ark 和 Qwen 都通过同一个显式 `socks5h` 代理；运行器会在任何 Provider 调用前检查代理端口。Qwen 固定快照不依赖原生 `response_format`，而是要求提示词返回 JSON，并只做一次本地 Pydantic 校验，不修复模型文本。Token 会写入忽略的原始结果；只有每一次应计费推理都返回完整 token、且显式配置 `LONG_BENCHMARK_*_CNY_PER_1K` 和 `LONG_BENCHMARK_ASR_CNY_PER_MINUTE` 时才输出成本，否则成本保留为 `null`，不能拿它做平局决策。任何可能已经计费的推理发生重试时，整次 arm-run 的成本都会保守地标为未知，不以最后一次成功响应低估成本。

## 人工金标复核

在模型运行前，由一位复核人完整播放两条视频并锁定金标版本；后续修订必须提高 `gold_version` 并记录原因。每个连续、可训练的示范或执行段单独标注开始与结束；讲解、休息和转场应单列，而不是把整段教程当成一个动作。只记录视频中明确表达的组数、次数、时长和休息；无法判断的字段保持 `null`，重量始终为 `null`。

- 7 分钟视频：覆盖罗马尼亚硬拉的所有实际示范段。
- 19 分钟视频：覆盖两项热身和五项正式动作，并锁定规范名及允许别名：绳索环绕、倒立推肩/俯卧撑、绳索夹胸、杠铃卧推、上斜卧推、实力推、颈后绳索臂屈伸。

金标是离线评分输入，不得进入任一模型提示词。评分前把金标标记为人工复核完成；未复核或越出原视频时间边界的金标必须使评分拒绝执行。主实验会把两份已复核金标的规范化内容 SHA-256 写入忽略的原始结果，评分时重新计算；即使 `gold_version` 未改变，只要内容发生变化也必须拒绝评分。

## 评分、结论与保留策略

每条 arm-run 记录完整覆盖、首个候选、上传、排队、预处理、ASR、视觉、融合、清理耗时和可用成本；评分报告动作事件 Precision / Recall / F1（tIoU 0.3 与 0.5）、边界误差、末段召回、重复/碎片、动作类型召回、参数准确率、无依据参数率、重量违规和三次稳定性。

有效 arm 必须覆盖全部块、三次均完成、没有重量字段、没有 Schema 或清理违规，并满足 Precision ≥ 0.90、Recall ≥ 0.80、F1@0.5 ≥ 0.75、无依据参数率 ≤ 2%。从断点恢复的 run 会保留在 Git 忽略的诊断结果中，但不计入有效三次或成本/时延比较。质量差距超过 3 个百分点时选择质量高者；否则依次比较成功率、完整覆盖耗时中位数和成本。报告只能得出以下之一：`Qwen 候选胜出`、`Seed 保持基线`、`无有效结论`。

单人金标只支持方向性的工程选择，不能宣称统计显著，也不能单凭本机代理结果冻结生产 Provider。

## 隐私、清理与可提交产物

临时本地文件只放在 `.benchmark-work/`、`benchmark-results/` 或 `tmp/`，这些目录均已被 Git 忽略。运行结束、取消或失败时要清理本地音频、静音块、联系表和临时目录；本轮 Seed 联系表以内嵌图像发送，不创建 Ark 文件。若后续路线创建 Ark 临时文件，必须删除并验证；Qwen 临时 OSS 对象只记录其 48 小时自动失效生命周期。预检、校准和主实验会在每次 Qwen 上传边界把脱敏生命周期记录原子写入 Git 忽略的审计 journal；传输结果不明确、取消、缺失或过期失效时间都会保留到期边界或记为清理失败。所有脱敏报告都会明确标记为“等待 Qwen 48 小时失效审计，禁止据此变更生产 Provider”；本地质量结论不构成生产接入授权。

### 当前本机门禁状态（2026-07-22）

2026-07-22 的真实重跑中，ASR、Seed 联系表视觉和 Seed 融合通过了两条来源的全部分级门禁；Qwen3-VL 通过配置、最小调用和 2 秒合成探针，但在 7 分钟来源的 8 秒真实片段临时 OSS multipart 上传阶段以 `qwen_upload_network_error_curl_exit_28` 失败，后续 60 秒门禁按协议跳过。该失败发生在模型推理和 Schema 校验之前，不能归因为 Qwen3-VL 的识别质量。预检回执不含路径、密钥、转录、对象键或原始响应；并发校准和 12 次完整 arm-run 均未启动，也没有宣布路线胜出。下一步是在目标服务器或另一条已批准的显式 SOCKS 出口复跑相同 8 秒和 60 秒门禁；不得用压缩输入或 Mock 偷换本轮 B 路线。

可写入仓库的只有代码、空白金标模板、示例清单和脱敏聚合研究结论。脱敏报告仅允许路线、模型/Skill/提示词版本、分块参数、通过状态、聚合质量指标、每分钟耗时中位数/范围、成本及结论；不得包含密钥、原始路径、视频/帧/音频、转录、提示词、原始响应、逐事件结果或 Provider 可识别请求内容。
