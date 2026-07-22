# 视觉 Provider 生产同构评测

## 用途

该评测只回答“哪条静音视觉路线可以进入生产主备排序”，不修改运行时配置，也不把一次真实调用写成生产结论。豆包 ASR 2.0 和语音证据解释在三条路线中保持相同，因此模型排序阶段不重复计分；候选路线进入 CloudBase 前仍必须通过完整 ASR、视觉、EvidenceReconciler 与 HTTP/SSE 五分钟 smoke。

冻结路线：

- `seed-mini`：`doubao-seed-2-0-mini-260428`
- `seed-lite`：`doubao-seed-2-0-lite-260428`
- `qwen-vl`：`qwen3-vl-flash-2026-01-22`

运行器直接复用生产 `LocalMediaProcessor`、连续静音 MP4 Prompt Contract、块内时钟校验和视觉去重实现。Qwen 预检还会验证私有 COS 的 ACL、服务端加密和生命周期合同。它不使用联系表、原生音画、滚动模型别名、模型修复重试或运行时 Mock。

## 私有材料

复制 [`provider-conformance-manifest.template.json`](provider-conformance-manifest.template.json) 到 Git 忽略的 `tmp/provider-conformance/manifest.json`，填写本地绝对路径、SHA-256、实测时长、规范动作名、允许别名和人工时间段。八条样本必须全部存在，每条路线固定运行三次；媒体、金标明细、逐次结果和模型输出都不得提交。

```powershell
pnpm benchmark:provider-conformance -- prepare
pnpm benchmark:provider-conformance -- preflight --real
pnpm benchmark:provider-conformance -- run --real
pnpm benchmark:provider-conformance -- score
```

`preflight` 对一条有动作金标的代表样本依次运行 2 秒、8 秒和最多 60 秒。任一步失败即停止，不进入完整矩阵。

## 判定

路线必须完成全部 24 个计分单元，并同时满足：Precision ≥ 0.90、Recall ≥ 0.80、F1@tIoU 0.5 ≥ 0.75，Schema 与时钟违规为零。视觉输出 Schema 不含重量或训练参数，因此该层无法伪造重量或参数；完整链路仍需独立检查 ASR 参数来源。

若合格路线 F1 相差超过 3 个百分点，选择质量更高者；否则依次比较成功率和每视频分钟完整覆盖中位时间。当前 Adapter 尚未提供可审计 Token 成本，报告会明确输出 `cost_status=not-measured`；前两项仍打平时返回 `qualified-routes-require-cost-review`，禁止凭路线顺序宣布冠军。

脱敏报告只包含路线、冻结模型、聚合质量、成功率、延迟和明确的证据状态，不包含本地路径、对象 key、签名 URL、动作明细、Prompt 或模型原文。真实评测未完成时，生产默认继续使用既有 Ark 主路线并关闭跨厂商降级。
