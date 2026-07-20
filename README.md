# 哈基米练臂力动

抖音 AI 创变者黑客松联赛当前主推进项目。项目把用户主动找到的健身视频，转化为可以确认、修正并自由编排的训练动作。

## 当前纵切片

首个可运行版本采用 Web-first 形态：

1. 用户选择受控视频源，在当前播放时间点击“添加动作”。
2. 后端并行运行语音识别与视频理解，再融合为动作候选。
3. 用户预览、修正并多选候选动作。
4. 动作进入同设备自动保存的方案草稿，可跨视频排序、复制、删除和编辑。

动作分析 Agent 只提出候选，不替用户确认动作或决定训练方案。当前切片不包含训练执行、卡路里、Pet、方案库、记录和分享海报。

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

`.env.local` 只由后端读取且必须保持 Git 忽略。受控演示视频通过 `HAKIMI_DEMO_VIDEO_PATH` 指向本地文件，视频不会复制进仓库。

## 验证

```powershell
pnpm check
pnpm test:e2e
```

真实 Ark + ASR 联调需在本地显式运行 smoke 脚本；CI 只使用合成媒体和测试专用适配器。

## 文档

- [项目进展与下一步（团队同步版）](docs/team/project-status-and-next-steps.md)
- [领域词汇表](CONTEXT.md)
- [首个动作分析纵切片技术设计与开发准入](docs/technical/action-analysis-vertical-slice.md)
- [架构决策](docs/adr)
- [原始项目方案](docs/source/哈基米练臂力动%20-%20抖音内置健身小程序项目方案.md)

`zhiyin` 与 `try-it` 已暂时搁置，原目录保持不变。
