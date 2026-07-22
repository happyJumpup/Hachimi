# 哈基米练臂力动（内部工作名）

项目把用户主动找到的健身视频，转化为可以确认、修正并自由编排的训练动作。正式产品名尚未决定；哈肌咪是 Agent／Pet 的角色名，不自动成为产品品牌。

下一轮原型以当前设备的本地视频导入为主、受控视频为可选快速体验兜底。浏览器同设备保存原视频，服务端只处理并清理临时副本；当前竞赛后端完整源文件上限为 5 分钟，部署能力仍以公共能力接口返回的真实值为准。

## 已完成的竞赛版基线

当前 Web MVP 已串起完整的本机训练闭环：

1. 用户点击“分析视频动作”后分析整段受控视频；语音与视觉并行，Agent 只提出可校正候选。
2. 跨视频选择动作，或创建没有参考视频的动作；草稿自动保存，可排序、复制和编辑参数。
3. 将草稿另存为本机方案，执行次数型或时长型训练，并在休息、离页或刷新后恢复。
4. 哈肌咪以五种状态陪练，可随时隐藏；训练档案只在本机用于卡路里约值。
5. 完整或提前结束的实际完成量进入训练记录；完整训练可生成 1080×1920 PNG 海报并分享或下载。

普通访客可使用明确标注的“快速体验方案”。它是静态产品样例，不是 AI 结果或运行时 Mock 回退。评委体验码只用于验证，验证后的访问级别通过安全 Cookie 保存。

主要页面：`/` 视频分析、`/plan` 方案编辑、`/mine` 我的训练、`/training` 训练执行、`/result/:recordId` 结果与海报。

## 技术栈

- `apps/web`：Vue 3、TypeScript、Vite、Pinia、Dexie
- `services/analysis-api`：Python 3.12、FastAPI、Pydantic、httpx
- `skills`：训练语音理解、动作视觉定位、动作候选融合

## 本地启动

前置环境：Node 24、pnpm 11、Python 3.12、uv。

```powershell
pnpm install
uv sync --project services/analysis-api
Copy-Item .env.example .env.local
pnpm dev
```

`.env.local` 只由后端读取且必须保持 Git 忽略。`HAKIMI_DEMO_VIDEO_PATH` 只配置受控快速体验；用户本地视频由浏览器主动选择，不通过环境变量或服务器路径导入。竞赛部署使用版本化来源清单和只读媒体缓存，视频不会复制进仓库。

测试 Provider 仅允许 `APP_ENV=test`，不能作为开发或生产回退。真实云配置缺失时，后端会明确失败。

## 验证

```powershell
pnpm check
pnpm test:e2e
pnpm api:generate
```

`pnpm test:e2e` 使用独立 loopback 端口、合成媒体和测试专用适配器，不复用本机已启动的开发服务。真实 Ark + 豆包流式语音识别 2.0 联调需在本地显式运行 `pnpm smoke:cloud`；CI 不注入云密钥。

## 文档

- [当前原型状态与下一步（团队同步版）](docs/team/project-status-and-next-steps.md)
- [领域词汇表](CONTEXT.md)
- [本地视频训练原型权威规格](docs/specs/local-video-training-prototype.md)
- [本地视频优先与可恢复覆盖分析 ADR](docs/adr/0013-local-video-import-and-recoverable-analysis.md)
- [Web 体验规范](docs/design/web-experience-guidelines.md)
- [完整 Web 架构](docs/technical/web-mvp-architecture.md)
- [训练场次、本地媒体与本地数据合同](docs/technical/training-session-contract.md)
- [历史竞赛版产品规格](docs/specs/competition-web-mvp.md)
- [历史竞赛部署与演示 Runbook](docs/release/competition-runbook.md)
- [架构决策](docs/adr)
- [原始项目方案](docs/source/哈基米练臂力动%20-%20抖音内置健身小程序项目方案.md)

`zhiyin` 与 `try-it` 已暂时搁置，原目录保持不变。
