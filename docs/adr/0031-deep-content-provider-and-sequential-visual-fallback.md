# 深内容理解 Provider 与顺序视觉降级

状态：已接受

日期：2026-07-23

## 背景

五分钟完整分析不能继续建立在旧的“单个局部窗口 + 联系表 + 11.5 秒证据预算”实现上。生产系统还必须区分三件事：代码能够接受的输入、某次部署实际配置的能力、已经通过真实云验证并允许对外声明的能力。此前文档在这三层之间混用了“60 秒默认”“300 秒已实现”和“300 秒已上线”。

## 决策

1. HTTP/SSE、Run Manager 和公开 `AnalysisCandidate` 保持兼容；内部使用 `ContentUnderstandingProvider.analyze(AnalysisRequest, emit) -> ContentUnderstandingResult`。
2. Provider 内部边界固定为 `MediaNormalizer`、`TimestampedTranscriber`、`SpeechEvidenceInterpreter`、`VisualEvidenceLocator` 和 `EvidenceReconciler`。Provider 的重试、预算、熔断和降级是代码策略，不由 TrainPal 或 LLM 规划。
3. 首个版本化 `ProviderProfile` 的代码上限为 300 秒、256 MiB、60 秒视觉块、10 秒重叠、最多 6 块。语音预算 45 秒，单次视觉尝试 20 秒，证据截止 170 秒，运行硬上限 180 秒并预留 10 秒清理；每个视觉 Provider 每块最多 2 次，总视觉调用最多 12 次。
4. 媒体只生成一份完整 16 kHz 单声道音频和连续静音 MP4 视觉块。视觉模型只返回块内 `0..chunk_duration` 时间，代码恰好做一次块偏移和一次来源范围偏移；Run Manager 不再偏移。
5. 旧“分析 Skill”改为 Provider 内部 Prompt Contract。每份合同固定输入媒体、时间坐标、输出 Schema、兼容模型族、版本和内容哈希。根 `skills/` 只保留训练编译、个性化调整和动作要点补充等领域 Skill。
6. ASR 首版继续使用豆包流式语音识别 2.0，并通过版本化、带哈希的健身热词合同向 `request.context` 注入请求级热词。录音文件极速版另行评测，不进入运行时降级。
7. 视觉候选固定为 Seed Mini、Seed Lite 和 Qwen3-VL Flash。生产同构评测通过硬门槛后，第一名成为主路线，最高排名的另一厂商路线才可成为备用。未通过评测时，备用功能必须关闭。
8. 自动降级按块顺序执行。可降级错误仅包括配置、网络／408／429／5xx、超时和 Schema 错误；有效空结果不降级，内容安全拒绝、取消、媒体错误和预算不足不降级。主路线最终失败后，当前块及剩余块直接使用备用，已经成功的块不重跑。
9. 主路线配置错误立即永久熔断至进程重启；瞬时错误在 60 秒内连续 3 次后熔断 60 秒，随后只允许一次半开探测。
10. Qwen 只接收静音块，使用 Chat Completions 的 `video_url` 与 JSON Object 模式，关闭思考并只做一次本地 Pydantic 校验，不调用模型修复 JSON。
11. Qwen 中转只使用私有腾讯 COS：随机对象名、不含用户或文件名、10 分钟只读签名 URL、AES-256 服务端加密、禁止公共 ACL、1 天生命周期。同块重试复用一个对象，所有终态主动删除；一次主动删除失败即关闭进程内后续备用分析。
12. 代码能力由 `LOCAL_ANALYSIS_MAX_SECONDS=300` 表示；对外能力由 `PUBLISHED_ANALYSIS_MAX_SECONDS` 表示，默认 60 秒。只有与当前部署 commit 绑定的五分钟 Canary 收据完整记录私有 smoke、三路并发、取消清理、熔断、COS 清理与 `B→C→B→C` 回滚，`/ready` 才允许发布 300 秒。

## 后果

- 取消、部分覆盖和 ASR 单分支失败仍能诚实返回；重启后内存运行明确失败并允许重试。
- Qwen/COS 代码存在不代表生产备用路线已启用；真实权限、私有 Bucket 安全配置、生产同构 A/B/C 和 CloudBase Canary 都是发布阻断项。
- 当前实现不扩展到 10–30 分钟。该范围需要对象存储、持久任务、Worker、幂等恢复和新的延迟合同。

## 取代范围

- 取代 [ADR-0012](0012-explicit-full-source-analysis-with-latency-budget.md) 中联系表、11.5 秒预算、局部触发窗口、Run Manager 二次偏移和 SSE 断开即取消的实现描述。
- 细化 [ADR-0030](0030-five-minute-chunked-analysis-and-signed-ffmpeg.md) 中 Provider 内部结构、视觉选型、发布能力与 Canary 门禁；其五分钟范围和签名 FFmpeg 决策继续有效。

