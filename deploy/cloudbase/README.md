# CloudBase 竞赛版部署基线

本目录只保存无秘密、无账户标识的 CloudBase Run 运行合同。`foundation-plan.json` 不是可直接导入腾讯云的 API 请求；真实环境 ID 由发布人在部署时从本机受控配置传入，不能写进 Git、回执或截图。本页描述当前候选应达到的状态，不表示这些配置已经在线上生效。

## 固定边界

| 项目 | 竞赛值 |
| --- | --- |
| 地域 / 服务 | `ap-shanghai` / `trainpal-demo` |
| 部署方式 | 仓库根目录 Dockerfile 源码构建 |
| 端口 / worker | `8000` / 1 |
| CPU / 内存 | 2 vCPU / 4 GiB |
| 实例数 | 非评审时 0；评审时 1；最大 1 |
| 应用 / CloudBase 配置请求超时 | 异步运行 180 秒 / 平台配置至少 240 秒；HTTP Access 曾观测到的 60 秒窗口只作历史限制证据 |
| 本地上传 | 后端合同：完整文件不超过 300 秒、256 MiB；CloudBase HTTP Access：20 MB；Web 安全上限：19,000,000 bytes |
| 视觉策略 | 60 秒分块、10 秒重叠、单块 20 秒、顺序执行 |
| 真实分析 | 公开单并发；无需评委码；成功尝试按会话与客户端 IP 各执行 600 秒冷却 |
| GYMTI | Ark／豆包最小数据增强；独立非排队 3 并发；第四条或模型失败立即本地降级 |
| 总预算 | 300 元人民币硬上限 |
| 最晚关闭 | `2026-07-25T00:00:00+08:00` |

同一个容器提供 SPA、FastAPI 和 SSE。活跃运行保存在单进程内存中，因此最大实例数不能大于 1。默认域名只用于短期开发演示；首次访问可能出现腾讯云风险提示，评委说明应写明“首次打开会看到腾讯云安全提示，请点击‘确定访问’进入演示”。

## 历史脱敏验收快照

| 阶段 | 不可变记录 | 结论 |
| --- | --- | --- |
| 版本 A | `trainpal-demo-007` / Build `2601400378` / `e5ba966451300c8027f174229e902acc0dd4ba94` | OA 私有探针、签名 FFmpeg、短样本与 295 秒样本通过 |
| 版本 B | `trainpal-demo-008` / Build `2601400393` / `81de88d0b7475797a51a4fdf73d334d5836dd529` | 前端合并、私有真实短样本通过 |
| 回滚 | `008 → 007 → 008` | 三次 `/health`、`/ready` 均为 200 |
| 历史公网配置 | `trainpal-demo-009`，复用版本 B 同一镜像 | 当时的 SPA/history、匿名隔离、三路评委 `202`、第四路 `429` 通过；三条完成结果均为 `partial` |
| 历史本地候选 | 精确 SHA 由 PR #9 发布回执记录 | 当时的双平台镜像审计、生产 `/ready`、匿名 GYMTI 本地降级与模型 trap 0 调用通过；未替换 `009` |

上述版本和配置均为 2026-07-23 早期候选的历史证据，不是当前最终版本。新候选必须以精确当前提交重新验证五条受控来源、公开单并发／双重 600 秒频控和 GYMTI Ark／豆包独立三并发；在取得新回执前不得声称已上线。已安排一次性任务在 2026-07-24 23:00（Asia/Shanghai）启动关停，确保本周六 0 点前禁用公网映射、恢复 `OA` 并把最小实例降为 0；任务本身的成功仍须以执行回执确认。

上述记录不包含环境 ID、域名、Pod、秘密值或真实分析内容。公网三路 `partial` 是真实质量限制，不能改写为 `complete`。

## 生产配置

秘密只通过 CloudBase 加密环境变量界面传入：

- `ARK_API_KEY`
- `VOLC_ASR_API_KEY`
- 新生成且彼此独立的 `JUDGE_ACCESS_CODE`（兼容访问验证；不作为公开分析门槛）
- 新生成的 `ACCESS_COOKIE_SECRET`

非秘密运行语义以 `foundation-plan.json` 为准。`required_*_environment_keys` 只表示发布前必须从当前稳定版安全继承的键；`release_managed_nonsecret_environment_keys` 与 `release_managed_secret_alias_environment_keys` 分别记录发布脚本新增的非秘密键和只按名称登记的秘密别名。发布脚本会把这两组名称与实际新增映射精确比对后才提交，回执仍只记录名称、不记录值。不能因为计划语义存在就假定变量已经注入。当前候选目标值包括：

```text
APP_ENV=production
APP_RELEASE_SHA=<injected-by-release-source.ps1>
ANALYSIS_PROVIDER=cloud
LOCAL_UPLOAD_ENABLED=true
LOCAL_ANALYSIS_MAX_SECONDS=300
LOCAL_UPLOAD_MAX_BYTES=268435456
ANALYSIS_EVIDENCE_TIMEOUT_SECONDS=170
ANALYSIS_VISUAL_CHUNK_SECONDS=60
ANALYSIS_VISUAL_OVERLAP_SECONDS=10
ANALYSIS_CHUNK_TIMEOUT_SECONDS=20
RUN_TIMEOUT_SECONDS=180
PUBLIC_ANALYSIS_CONCURRENCY=1
JUDGE_ANALYSIS_CONCURRENCY=0
IMAGEIO_FFMPEG_EXE=/opt/trainpal/ffmpeg/bin/ffmpeg
FFMPEG_BUILD_RECEIPT_PATH=/opt/trainpal/ffmpeg/receipt.json
WEB_STATIC_ROOT=/workspace/apps/web/dist
TRUSTED_PROXY_CIDRS=
GYMTI_LLM_ENABLED=true
GYMTI_LLM_RETENTION_CONFIRMED=true
GYMTI_LLM_CONCURRENCY=3
GYMTI_LLM_MODEL=doubao-seed-2-0-mini-260428
GYMTI_LLM_BASE_URL=<operator-verified-ark-compatible-base-url>
SOURCE_MANIFEST_PATH=/workspace/competition/media-manifest.json
SOURCE_MEDIA_ROOT=/workspace/tmp/controlled-media
PUBLIC_MEDIA_BASE_URL=<operator-supplied-https-media-base>
```

空 `TRUSTED_PROXY_CIDRS` 表示只使用直连对端地址；应用不会信任任意 `X-Forwarded-For`。在腾讯云提供并实测一个窄可信代理网段前，不得猜测或使用全网 CIDR。

竞赛生产必须同时配置 `SOURCE_MANIFEST_PATH`、`SOURCE_MEDIA_ROOT` 和 `PUBLIC_MEDIA_BASE_URL`，并从版本化清单同步恰好五条已授权来源。任一字段缺失、条目数不为五、缓存多余／缺失或时长／哈希不符都使 `/ready` 失败。视频不进入 Git 或镜像；容器只包含清单，启动包装器在启动 Uvicorn 前把五条媒体同步到只读运行缓存并失败关闭。

## 源码包与镜像门槛

CloudBase 官方源码部署要求代码目录根部包含 Dockerfile；CLI 使用 `--source` 上传该目录。仓库 `.dockerignore` 排除 Git、环境文件、媒体、测试产物、依赖目录、文档和 `questionaire/` 参考原型，任何真实视频都不能进入构建上下文。`git archive` 还通过 `.gitattributes` 排除参考原型，`release-source.ps1` 在上传前再次检查 ZIP 内容并失败关闭。

Docker 多阶段构建从 FFmpeg 8.1.2 官方签名源码生成共享、LGPL-only 运行时。镜像内只允许 `/opt/trainpal/ffmpeg/bin/ffmpeg` 一份二进制，`/ready` 和镜像审计都校验同目录构建收据，并拒绝 GPL、nonfree、版本、配置或哈希不符。

本地门槛：

```powershell
.\deploy\cloudbase\preflight.ps1
docker build --tag trainpal-five-minute:local .
```

随后执行 `deploy/verify-competition-image.ps1 trainpal-five-minute:local`；Linux CI 还执行等价的 Bash 门禁。通过项包括逐层无秘密/媒体扫描、180 个登记视觉哈希、唯一 FFmpeg 与收据、非 root 用户、五来源运行 Fixture 与生产 `/health`／`/ready`、SPA/history，以及 GYMTI 配置模型调用、非法／失败响应的本地降级和无问卷内容日志。独立三并发／第四路降级由 API 聚焦测试与目标候选 smoke 另行覆盖；镜像门禁通过不等于目标 CloudBase 已部署成功。

## A / B 发布顺序

1. 发布人先把真实环境 ID放入当前 PowerShell 进程的专用变量，不能写入仓库或回执：

   ```powershell
   $env:TRAINPAL_CLOUDBASE_ENV_ID = '<operator-supplied>'
   ```

2. 在私有/OA 访问状态下，以交互确认方式部署后端基建版本 A：

   ```powershell
   npx --yes --package @cloudbase/cli@3.6.4 tcb `
     --env-id $env:TRAINPAL_CLOUDBASE_ENV_ID `
     --region ap-shanghai `
     cloudrun deploy `
     --serviceName trainpal-demo `
     --port 8000 `
     --source .
   ```

   不使用 `--force`。交互页必须再次核对目标服务、访问方式和价格影响。源码构建日志通过后，先验证容器、签名 FFmpeg、`/health`、`/ready`、SSE 和一条真实短视频；版本 A 及其兼容环境配置必须保留。

3. 最终前端提交后，重新生成 OpenAPI 类型、跑完整前端/E2E 和窄屏检查，再从精确候选 SHA 部署 `GRAY` 候选。若存在公开稳定版，`release-source.ps1` 必须严格保留现有访问配置，并让候选从 0% 流量开始；发布人核对目标 Provider 账号的训练／内容留存设置后，必须在命令中显式传入 `-ConfirmGymtiProviderRetention`，否则脚本失败关闭。CloudBase 源码包构建是当前发布路线；GHCR 拉取只保留为曾失败的历史尝试，不是主路线或回滚依赖。

4. 候选构建完成且稳定版 100%／候选 0% 状态唯一确认后，先运行兼容文件名 `run-private-canary.ps1` 的 full-FLOW 身份事务：临时把候选提升到公开流量 100%，从公开入口核对 `/health` 返回精确候选 SHA 且 `/ready` 为 `ready`，然后无论成功或失败都在 `finally` 恢复并验证稳定版 100%／候选 0%。该事务不接收路由头或媒体，不调用分析或 GYMTI Provider；恢复失败按 P0 处理。

   身份事务通过后，才在受控公开窗口重新提升候选并运行数据面门槛：先用最短受控来源验证五来源、公开单并发、SSE、终态与会话／IP 冷却；待该冷却窗口确实结束后，再用 295 秒授权样本验证本地上传。随后补齐 GYMTI 三并发／第四路降级和候选→稳定版→候选回滚。任一门槛失败都恢复稳定版，不发布新链接。2026-07-23 的旧定向 canary 未命中候选只保留为历史失败证据，不能替代当前 full-FLOW 身份事务。

5. 仅在评审窗口把最小实例数设为 1，其余时间恢复 0。费用接近 300 元时先把公开真实分析并发归零；到最晚关闭时间禁用公共入口并缩容。

历史实测中 CloudBase HTTP Access 对请求体表现为 20 MB 上限，并让上传创建在约 60 秒有效窗口内返回；这不是应用的“60 秒默认分析能力”。当前平台配置仍请求至少 240 秒，但分析创建必须尽快返回 `202`，随后客户端通过快照和 SSE 等待最长 180 秒的应用终态。约 24.9 MB 与 97 MB 请求曾在到达应用前被拒绝，约 15.1 MB 的 295 秒样本成功，因此前端采用 19 MB 安全边界。

紧急关闭时先禁用唯一的公网根路由，立即阻断新公网请求；脚本默认失败关闭且不输出环境 ID 或域名。`-CheckOnly` 只核对目标，不改状态：

```powershell
.\deploy\cloudbase\disable-public-route.ps1 `
  -EnvironmentId $env:TRAINPAL_CLOUDBASE_ENV_ID `
  -Domain '<operator-supplied-default-domain>' `
  -CheckOnly `
  -OutputPath '<private-receipt-path>'
```

确认目标唯一后，移除 `-CheckOnly` 即禁用根路由。路由关闭只负责立即停止公网入口；随后仍须通过官方 `UpdateCloudRunServer` 复用当前镜像，把 `AccessTypes` 收紧为 `OA`、`MinNum` 降为 0、`MaxNum` 保持 1。不得删除服务、重建源码或修改 Provider 配置来代替缩容。

## 脱敏回执

只读脚本仅输出服务名、版本、Build ID、提交 SHA、资源规格、伸缩、访问类型和环境变量名称，不输出环境 ID、域名、Pod ID、日志正文或环境变量值：

```powershell
.\deploy\cloudbase\inspect-service.ps1 `
  -EnvironmentId $env:TRAINPAL_CLOUDBASE_ENV_ID `
  -CommitSha '<full-lowercase-git-sha>' `
  -OutputPath '<private-receipt-path>'
```

`release-source.ps1` 从精确、干净的 Git 提交生成源码包，在内存中保留生产秘密，固定五分钟运行配置，并请求初始 0% 流量的 `GRAY` 源码发布。它保留当前 `OA` 或 `OA + PUBLIC` 访问策略，注入 `APP_RELEASE_SHA`，并要求发布人显式确认 GYMTI Provider 留存边界：

```powershell
.\deploy\cloudbase\release-source.ps1 `
  -EnvironmentId $env:TRAINPAL_CLOUDBASE_ENV_ID `
  -CommitSha '<full-lowercase-git-sha>' `
  -ExpectedCurrentVersion '<operator-confirmed-current-version>' `
  -PublicMediaBaseUrl '<operator-supplied-https-media-origin>' `
  -ConfirmGymtiProviderRetention `
  -OutputPath '<private-receipt-path-outside-repository>'
```

`-ConfirmGymtiProviderRetention` 不是默认值或自动探测；只有发布人已在目标账号核对训练复用和内容留存设置时才可传入。缺少该开关时脚本会在打包和云端变更前失败关闭。发布回执只记录零流量请求、名称集合和哈希；实际流量与身份仍由部署后的事务核对。

`run-private-canary.ps1` 仅为兼容保留原文件名；当前行为是 full-FLOW 公开身份事务，不是私有或请求头 canary。它从精确稳定版 100%／候选 0% 开始，临时 `promote` 到候选 100%，通过公开入口核对 `/health` 的 `release_sha` 与预期完整 SHA 完全一致并确认 `/ready`，最后在 `finally` 中 `restore` 且验证稳定版 100%／候选 0%。它不接收媒体，也不执行真实分析：

```powershell
.\deploy\cloudbase\run-private-canary.ps1 `
  -EnvironmentId $env:TRAINPAL_CLOUDBASE_ENV_ID `
  -PublicBaseUrl '<operator-supplied-https-origin>' `
  -ExpectedStableVersion '<operator-confirmed-current-stable-version>' `
  -ExpectedCandidateCommitSha '<full-lowercase-git-sha>' `
  -OutputDirectory '<private-directory-outside-the-repository>'
```

事务回执仅包含服务名、候选提交 SHA、稳定版本、`health`／`ready` 聚合状态和恢复状态；不记录环境 ID、公开 origin 或任何内容。恢复失败高于主检查结果：必须立即阻断发布并人工恢复稳定流量。

身份事务通过并恢复稳定流量后，在另一个受控公开窗口重新提升候选。先用 `--controlled-shortest` 从恰好五条来源中选择最短项，验证一个公开请求 `202`、并发第二个请求 `429 + Retry-After`、SSE 终态、覆盖合同，以及已接纳会话和新会话同 IP 的冷却：

```powershell
python .\deploy\cloudbase\public-canary.py `
  --public-base-url '<operator-supplied-https-origin>' `
  --controlled-shortest `
  --output '<private-controlled-canary-receipt>'
```

该次运行会建立会话／IP 冷却。只有公开能力接口重新显示可分析、实际冷却窗口已经结束后，才用 `--media` 对 295 秒、仍处于 19 MB Web 安全边界内的授权样本执行同一公开并发、SSE、终态和冷却合同：

```powershell
python .\deploy\cloudbase\public-canary.py `
  --public-base-url '<operator-supplied-https-origin>' `
  --media '<private-authorized-295-second-media>' `
  --output '<private-upload-canary-receipt>'
```

两类公开 canary 回执都只保留输入种类、受控目录计数或媒体字节数、最短项时长、并发／终态／覆盖聚合计数和冷却秒数；不记录受控来源 ID、标题、origin、对象路径、本地路径、文件名、分析候选或内容。回执放在团队受控位置，不提交 Git；原视频、密钥、账户标识和可搜索的真实分析内容同样不得提交。

GYMTI 使用独立的公开 canary：先验证一次 LLM 选题和一次非空 LLM 正式结果叙事，再并发四次选题并要求精确三次 `llm`、一次 `local_fallback`：

```powershell
python .\deploy\cloudbase\public-gymti-canary.py `
  --public-base-url '<operator-supplied-https-origin>' `
  --output '<private-gymti-canary-receipt-outside-repository>'
```

回执只包含 schema、服务名、选题／叙事通过布尔值、并发来源计数和 Provider 模型名；不记录 origin、Cookie、回答、题目、选项、正式结果、原因 ID、叙事文本或响应正文。
