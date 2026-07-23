# GYMTI 哈基米问卷

> 状态：参考原型。该目录保留独立问卷的原始实现与素材，用于设计和实现追溯；TrainPal 正式实现位于 `apps/web` 与 `services/analysis-api`。本目录不参与根工作区测试、CloudBase 源码包、容器构建或线上部署。

> 本目录中的 DeepSeek 环境变量、请求载荷、日志和 API 仅描述历史参考原型，不是当前生产配置或隐私合同。竞赛生产路线以 [ADR-0044](../docs/adr/0044-gymti-uses-ark-doubao-minimal-data-and-independent-concurrency.md) 为准；DeepSeek 只保留为待发 Issue #5 的脱敏评估输入。

`questionaire` 是一个可独立运行的 GYMTI 健身人格与哈基米教练匹配问卷。

它会在 5-8 道题内判断用户的 GYMTI 健身人格，并按匹配度推荐哈基米教练。人格结果固定，用户如果不满意默认哈基米，可以继续按匹配度顺序切换，直到选中满意的教练。

## 内容

- `server.js`：本地 HTTP 服务、API、图片服务、DeepSeek 调用与兜底逻辑。
- `questionnaire.js`：Markdown 题库解析、确定性打分、结果排序。
- `public/`：抽屉式问卷前端页面。
- `data/GYMTI与桌宠联合问卷设计 1.md`：问卷题库。
- `data/PYTI测评结果.md`：人格结果说明。
- `assets/GYMTI-images/`：GYMTI 人格结果图。
- `assets/cat-fitness-avatars/`：哈基米教练头像。
- `GYMTI-LLM评分体系.md`：评分体系和 LLM 使用边界说明。

## 运行

以下命令只用于本地查看参考原型，不属于根仓库构建或发布步骤。

```powershell
cd questionaire
Copy-Item .env.example .env
node server.js
```

打开：

```text
http://localhost:5178
```

不配置 API key 也可以运行，系统会使用本地选题和模板文案兜底。

## 可选环境变量

- `DEEPSEEK_API_KEY`：DeepSeek API key，不要提交到 git。
- `DEEPSEEK_MODEL`：默认 `deepseek-chat`。
- `PORT`：默认 `5178`。
- `MIN_QUESTIONS`：默认 `5`。
- `MAX_QUESTIONS`：默认 `8`。
- `GYMTI_QUESTIONNAIRE_PATH`：默认 `data/GYMTI与桌宠联合问卷设计 1.md`。
- `CAT_AVATAR_DIR`：默认 `assets/cat-fitness-avatars`。
- `GYMTI_IMAGE_DIR`：默认 `assets/GYMTI-images`。

## API

- `GET /api/health`：查看题库数量和 LLM 是否启用。
- `POST /api/start`：开始新会话，第一题固定为健身神报告题，避免首屏等待模型选题。
- `POST /api/answer`：提交答案。
- `POST /api/finish`：达到最低题数后提前出结果。
- `POST /api/profile`：提交或跳过身高、体重、性别信息后生成结果。
- `GET /avatar?type=...`：哈基米教练头像。
- `GET /gymti-image?type=...`：GYMTI 人格结果图。

## 设计边界

- GYMTI 人格与哈基米教练是两条独立结果，相关但不强绑定。
- LLM 可以选择下一题和撰写解释，但不能覆盖本地确定性打分结果。
- “没有符合我的描述”不加分，只降低结果置信度。
- 有效信号为 0 时不会默认给出 `HIDE + 温柔陪伴型`，而是返回待确认结果。
- 结果页不展示分数或评分过程。
