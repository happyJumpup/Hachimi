# TrainPal

> `questionaire/` 保留队友提交的 GYMTI 独立参考原型，仅用于设计与实现追溯；正式产品入口是 `apps/web` 与 `services/analysis-api`，参考原型不进入 CloudBase 源码包、容器或线上服务。

TrainPal 是你的专属训练伙伴：把用户主动找到的健身视频转成可以确认、调整并立即执行的训练，并由同一个小猫教练在规划、训练和结果阶段持续陪伴。

TrainPal 同时是产品名、用户可见的唯一 Agent 和小猫教练身份。内部由可替换的内容理解 Provider、训练编译 Skill、个性化调整 Skill、动作要点补充 Skill 与确定性训练引擎协作；用户始终保留最终决定权。

当前竞赛版以当前设备的本地视频导入为主、受控视频为可选快速体验兜底。浏览器同设备保存原视频，服务端只处理并清理临时副本；完整源文件上限为 5 分钟，部署能力仍以公共能力接口返回的真实值为准。

## 本地候选已完成的竞赛版基线

当前 Web MVP 已在本地候选串起完整的本机训练闭环：

1. 用户在独立首页选择本地视频，并在明确点击后启动真实整段分析；语音与视觉并行，TrainPal 只提出可校正候选。
2. 跨视频选择动作，或创建没有参考视频的动作；当前方案自动保存，可排序、复制和编辑参数。
3. 将当前方案另存为本机方案，执行次数型或时长型训练，并在休息、离页或刷新后恢复。
4. TrainPal 以五种状态陪练，可随时隐藏；训练档案只在本机用于卡路里约值和个性策略弱参考。
5. GYMTI v1 使用共享版本化合同完成 5–8 题测评、七猫单一推荐、明确确认／改选和同设备恢复；目标竞赛部署使用 Ark／豆包最小数据增强与独立三并发门，拥塞、失败或留存边界未确认时立即使用确定性本地选题与模板叙事。
6. 完整或提前结束的实际完成量进入训练记录；完整训练可生成 1080×1920 PNG 海报并分享或下载。

当前部署合同要求普通访客可使用五条明确标注、按时长排列的受控视频发起真实分析；同一时刻只接纳一条公开分析，并按会话与客户端 IP 各限制 600 秒内一次。评委体验码不再是公开真实分析的必要条件。静态“快速体验方案”仍是产品样例，不是 AI 结果或运行时 Mock 回退。该新合同尚无当前 CloudBase 候选的线上通过回执，不得把本地实现或历史版本结果表述为已上线成功。

主要页面：`/` 首页与导入、`/analysis` 分析任务、`/plan` 方案编辑、`/personalize` 个性化、`/train` 训练中心、`/training` 训练执行、`/result/:recordId` 结果与海报、`/mine` 我的。开发环境另提供静态 Fixture 设计画廊 `/__design/trainpal` 和七猫动画预览台 `/__design/trainpal/pets`，生产构建不注册这两个路由。

## 技术栈

- `apps/web`：Vue 3、TypeScript、Vite、Pinia、Dexie
- `services/analysis-api`：Python 3.12、FastAPI、Pydantic、httpx
- `skills`：训练编译、个性化调整、动作要点补充的版本化领域能力

## 本地启动

前置环境：Node 24、pnpm 11、Python 3.12、uv。

```powershell
pnpm install
uv sync --project services/analysis-api
Copy-Item .env.example .env.local
pnpm dev
```

`.env.local` 只由后端读取且必须保持 Git 忽略。`HAKIMI_DEMO_VIDEO_PATH` 只配置受控快速体验；用户本地视频由浏览器主动选择，不通过环境变量或服务器路径导入。竞赛部署使用版本化来源清单和只读媒体缓存，视频不会复制进仓库。

七猫运行资源已经是 WebP。需要从设计交付包重新生成时，使用项目内 Sharp 脚本；`<raw-pets-directory>` 应直接包含 `hotblood`、`gentle` 等七个目录：

```powershell
pnpm --filter @hachimi/web pets:build -- --source <raw-pets-directory>
```

脚本只转码清单中的 168 个动画 PNG，不复制 `fullbody.png`，也不把原始 PNG 放入运行目录。

测试 Provider 仅允许 `APP_ENV=test`，不能作为开发或生产回退。真实云配置缺失时，后端会明确失败。

竞赛生产计划显式启用 Ark／豆包 GYMTI 模型增强；调用只发送版本化稳定 ID、服务端派生信号和已经成立的结构化结果，使用独立三并发门且不消耗真实视频分析额度。第四条并发、模型错误或供应商留存边界未确认时立即走本地降级。问卷原型 `questionaire/` 只作追溯参考，不参与根工作区测试、源码包、容器或线上服务；正式事实源为 `contracts/gymti-questionnaire.v1.json`。

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
- [当前竞赛 Web 产品规格](docs/specs/competition-web-mvp.md)
- [冻结的移动体验 Brief](docs/design/trainpal-mobile-experience-brief.md)
- [历史本地视频训练原型规格](docs/specs/local-video-training-prototype.md)
- [本地视频优先与可恢复覆盖分析 ADR](docs/adr/0013-local-video-import-and-recoverable-analysis.md)
- [GYMTI 主应用确认边界 ADR](docs/adr/0031-questionnaire-recommends-main-app-confirms-coach-style.md)
- [GYMTI 共享版本化合同 ADR](docs/adr/0040-gymti-uses-one-versioned-json-contract.md)
- [公开分析单并发与频控 ADR](docs/adr/0043-public-analysis-uses-one-slot-and-session-ip-cooldown.md)
- [GYMTI Ark／豆包最小数据与独立并发 ADR](docs/adr/0044-gymti-uses-ark-doubao-minimal-data-and-independent-concurrency.md)
- [Web 体验规范历史入口](docs/design/web-experience-guidelines.md)
- [完整 Web 架构](docs/technical/web-mvp-architecture.md)
- [训练场次、本地媒体与本地数据合同](docs/technical/training-session-contract.md)
- [竞赛部署与演示 Runbook](docs/release/competition-runbook.md)
- [架构决策](docs/adr)
- [历史原始项目方案](docs/source/哈基米练臂力动%20-%20抖音内置健身小程序项目方案.md)

`zhiyin` 与 `try-it` 已暂时搁置，原目录保持不变。
