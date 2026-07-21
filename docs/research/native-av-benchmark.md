# Seed / Qwen 原生音视频能力门禁

## 决策边界

本工具只回答“一分钟受控素材上，哪条路线的质量领先”。它不修改生产 Provider，
不把本地上传耗时当作生产延迟，也不会在本地运行 7/19 分钟整段测试。

七条冻结路线和模型 ID 由 `benchmark/native-av-benchmark.example.json` 定义。
Qwen3-VL 仅处理无声视频；Qwen3.5-Omni 才处理原生音画。S-MOD 与 Q-MOD
将纯视觉结果与同一 repetition 的 ASR 结果并行生成，再由 Seed Mini 融合；纯视觉
结果同时作为控制结果保存，不重复调用视觉模型。

Qwen 临时 OSS 仅属于本 benchmark。官方规定该 URL 与账号和精确模型绑定，48 小时
后自动清除，且只适合开发测试，因此不得接入生产分析 API。参考：
[Qwen 临时文件](https://help.aliyun.com/en/model-studio/get-temporary-file-url)、
[Qwen Omni](https://help.aliyun.com/en/model-studio/qwen-omni)。

## 本地安全边界

- 创建 `.env.benchmark.local`，只包含 `ARK_API_KEY`、`DASHSCOPE_API_KEY`、
  `VOLC_ASR_API_KEY` 和非敏感配置；该文件已被 Git 忽略。
- 所有 Ark、DashScope policy、OSS 上传和推理请求都显式使用
  `socks5h://127.0.0.1:7897`，Fake-IP 且无显式代理时拒绝执行。
- Bearer Key、OSS policy 和签名通过 curl stdin config 传递，不进入命令行 argv。
- Ark 文件在每个门禁或完整矩阵结束时 DELETE，并通过 GET 404/410 验证。
- Qwen 临时 URL 记录 48 小时到期时间，清理结果明确记为
  `retained_until_expiry_recorded`；官方不提供查询或主动删除能力，不伪造删除成功。
- Provider 原始响应、转录和本地路径不写入报告；正式运行只把规范化候选写到
  Git 忽略目录。
- curl 和 FFmpeg 使用可终止并等待退出的异步子进程；Ark multipart 上传不自动重放，
  避免响应丢失时制造无法追踪的重复文件。

## 运行顺序

先从示例复制一份 manifest 到 Git 忽略目录，填写三个来源和两个经团队复核的
60 秒动作密集窗口。然后：

```powershell
pnpm benchmark:av:prepare --manifest .benchmark-work/native-av.json
pnpm benchmark:av:gold --manifest .benchmark-work/native-av.json
pnpm benchmark:av:preflight --manifest .benchmark-work/native-av.json --real
pnpm benchmark:av:run --manifest .benchmark-work/native-av.json --output benchmark-results/runs.json --real
pnpm benchmark:av:score --manifest .benchmark-work/native-av.json --results benchmark-results/runs.json --output benchmark-results/report.json
```

`prepare` 会为每条样本生成规范音画 MP4、复制视频码流的无声 MP4 和 16 kHz
单声道 PCM WAV。只有确认源视频没有音轨时才生成等长静音 WAV；三份派生媒体分别
冻结 SHA-256 并校验实际时长。`preflight` 按 `Seed 音画 / Seed 纯视觉 / Seed 音频 /
Seed 文本 / Qwen 音画 / Qwen 纯视觉 / Qwen 音频 / ASR` 八个依赖组件分别执行，
严格依次检查配置与精确模型、无媒体最小调用、2 秒合成音视频、8 秒真实片段和完整
一分钟样本；
权限、模型、Schema 或媒体错误不会重试；可安全重放的请求遇到网络错误、408、429、
5xx 最多重试两次，multipart 上传因无法证明幂等而不自动重放。
预检会在 `.benchmark-work/preflight-receipt.json` 写入与 manifest 字节哈希、固定模型
和通过组件绑定的六小时凭据；正式 `run` 必须消费覆盖所选路线全部依赖的凭据。

正式结果将上传和推理分开计时，并记录预处理、首个 HTTP 响应字节、首个可解析候选、
最终结果、融合以及每个媒体句柄的清理耗时/清理语义。ASR 不跨路线复用推理结果，
因此 AB/BA 顺序不会把 ASR 时间只记到先运行的路线。Token 会聚合；在未冻结各模型
和 ASR 的计价表前，成本字段保持 `null`，不得凭空估价。

正式评分前，三份金标必须由团队播放复核，将 `status` 改为 `reviewed` 并填写
`reviewed_by`。评分还会校验金标 `sample_id`、manifest 状态和事件时间边界；未经
有效人工复核，`score` 会拒绝运行。

## 指标与晋级

评分采用动作规范名/冻结别名与 tIoU 的确定性一对一最大匹配，分别报告 0.3 和
0.5；同时报告边界误差、末 10% 召回、重复、碎片、训练参数准确率、无依据参数、
重量违规和三次延迟的中位数与范围。少于 20 个延迟样本不报告 P95。

硬门禁为 Precision ≥ 0.90、Recall ≥ 0.80、F1@0.5 ≥ 0.75、无依据参数率
≤ 2%、重量违规为 0，并且没有 Schema 或清理违规。本地结论只能写“质量领先路线”。
服务器和 COS/CDN 可用后，再让最多四条路线进入 7/19 分钟长视频阶段。
