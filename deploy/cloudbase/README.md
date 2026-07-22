# CloudBase 竞赛版部署基线

本目录只保存无秘密、无账户标识的 CloudBase Run 运行合同。`foundation-plan.json` 不是可直接导入腾讯云的 API 请求；真实环境 ID 由发布人在部署时从本机受控配置传入，不能写进 Git、回执或截图。

## 固定边界

| 项目 | 竞赛值 |
| --- | --- |
| 地域 / 服务 | `ap-shanghai` / `trainpal-demo` |
| 部署方式 | 仓库根目录 Dockerfile 源码构建 |
| 端口 / worker | `8000` / 1 |
| CPU / 内存 | 2 vCPU / 4 GiB |
| 实例数 | 非评审时 0；评审时 1；最大 1 |
| 应用 / 平台超时 | 180 秒 / 至少 240 秒 |
| 本地上传 | 最长 300 秒；CloudBase 公网 20 MB envelope，前后端对外固定 19 MiB；Provider 受控来源内部上限 256 MiB；公开时长默认 60 秒 |
| 视觉策略 | 60 秒分块、10 秒重叠、单块 20 秒、顺序执行 |
| 真实分析 | 匿名 0 并发；评委码 3 并发 |
| 总预算 | 300 元人民币硬上限 |
| 最晚关闭 | `2026-07-25T00:00:00+08:00` |

同一个容器提供 SPA、FastAPI 和 SSE。活跃运行保存在单进程内存中，因此最大实例数不能大于 1。默认域名只用于短期开发演示；首次访问可能出现腾讯云风险提示，评委说明应写明“首次打开会看到腾讯云安全提示，请点击‘确定访问’进入演示”。

## 生产配置

秘密只通过 CloudBase 加密环境变量界面传入：

- `ARK_API_KEY`
- `VOLC_ASR_API_KEY`
- 只有版本 C 启用备用路线时才注入 `QWEN_API_KEY` 和私有 COS 的 `COS_SECRET_ID`、`COS_SECRET_KEY`
- 新生成且彼此独立的 `JUDGE_ACCESS_CODE`
- 新生成的 `ACCESS_COOKIE_SECRET`

非秘密值以 `foundation-plan.json` 中的名称集合为准。关键值包括：

```text
APP_ENV=production
ANALYSIS_PROVIDER=cloud
LOCAL_UPLOAD_ENABLED=true
LOCAL_ANALYSIS_MAX_SECONDS=300
PUBLISHED_ANALYSIS_MAX_SECONDS=60
LOCAL_UPLOAD_MAX_BYTES=19922944
ANALYSIS_MAX_SOURCE_BYTES=268435456
ANALYSIS_SPEECH_TIMEOUT_SECONDS=45
ANALYSIS_EVIDENCE_DEADLINE_SECONDS=170
ANALYSIS_CLEANUP_RESERVE_SECONDS=10
ANALYSIS_VISUAL_CHUNK_SECONDS=60
ANALYSIS_VISUAL_OVERLAP_SECONDS=10
ANALYSIS_CHUNK_TIMEOUT_SECONDS=20
ANALYSIS_MAX_VISUAL_CHUNKS=6
ANALYSIS_MAX_ATTEMPTS_PER_VISUAL_PROVIDER=2
ANALYSIS_MAX_VISUAL_CALLS=12
VISUAL_FALLBACK_ENABLED=false
QWEN_VISUAL_MODEL_ID=qwen3-vl-flash-2026-01-22
COS_SIGNED_URL_TTL_SECONDS=600
COS_LIFECYCLE_DAYS=1
RUN_TIMEOUT_SECONDS=180
PUBLIC_ANALYSIS_CONCURRENCY=0
JUDGE_ANALYSIS_CONCURRENCY=3
IMAGEIO_FFMPEG_EXE=/opt/trainpal/ffmpeg/bin/ffmpeg
FFMPEG_BUILD_RECEIPT_PATH=/opt/trainpal/ffmpeg/receipt.json
WEB_STATIC_ROOT=/workspace/apps/web/dist
TRUSTED_PROXY_CIDRS=
```

空 `TRUSTED_PROXY_CIDRS` 表示只使用直连对端地址；应用不会信任任意 `X-Forwarded-For`。在腾讯云提供并实测一个窄可信代理网段前，不得猜测或使用全网 CIDR。

当前版本可以只有本地上传入口。未配置受控来源时，`SOURCE_MANIFEST_PATH`、`SOURCE_MEDIA_ROOT` 和 `PUBLIC_MEDIA_BASE_URL` 均保持为空，`GET /sources` 返回空列表；如果未来启用受控来源，三项必须同时配置并通过媒体哈希检查。

## 源码包与镜像门槛

CloudBase 目标环境已实测成功的默认路径是源码包构建。发布人必须通过 `deploy/cloudbase/deploy-source.ps1` 部署：脚本用 `git archive` 从指定的完整 commit 生成干净包，拒绝环境文件、密钥格式和原始媒体，在包内注入 `.trainpal-build-metadata.json`，再调用 CLI 3.6.4 的 `--source`。禁止直接对工作区执行 `--source .`，以免未提交文件、密钥或缺失构建指纹进入远程构建。

GHCR 不可变 digest 路线保留为可选供应链方案，但目标 CloudBase 环境此前拉取失败。在同一目标环境完成真实拉取、启动和回滚演练前，不得把 `--imageUrl` 标为生产可用，也不得替换上述源码包默认路径。仓库 `.dockerignore` 仍排除 Git、环境文件、媒体、测试产物、依赖目录和文档。

Docker 多阶段构建固定全部基础镜像 digest，并从 FFmpeg 8.1.2 官方签名源码生成共享、LGPL-only 运行时。镜像内只允许 `/opt/trainpal/ffmpeg/bin/ffmpeg` 一份二进制，`/ready`、五分钟 Canary 收据和镜像审计都绑定同目录构建收据，并拒绝 GPL、nonfree、版本、配置或哈希不符；发布记录仍须保存 CloudBase Build ID。

本地门槛：

```powershell
.\deploy\cloudbase\preflight.ps1
$trainpalCommit = (git rev-parse HEAD).Trim()
docker build `
  --build-arg "TRAINPAL_BUILD_COMMIT_SHA=$trainpalCommit" `
  --tag trainpal-five-minute:local .
```

发布 300 秒时，构建上下文必须包含与发布 commit 一致的元数据。默认源码包路线由 `package-source.ps1` 写入；可选本地/GHCR 镜像路线由 `TRAINPAL_BUILD_COMMIT_SHA=<40 位小写 commit>` 构建参数写入。仅设置运行时 `DEPLOYMENT_COMMIT_SHA` 不能解锁 300 秒；没有构建指纹的部署必须保持 60 秒 fail-closed。

随后在 Bash 环境执行 `deploy/verify-competition-image.sh trainpal-five-minute:local '<40-char-commit>'`。通过项包括逐层无秘密/媒体扫描、镜像构建 commit 指纹、唯一 FFmpeg 与收据、非 root 用户、容器启动、`/health`、SPA 根路由和 history fallback。

`LOCAL_ANALYSIS_MAX_SECONDS` 是代码和私有验收上限；`GET /api/v1/capabilities` 只返回 `PUBLISHED_ANALYSIS_MAX_SECONDS`。私有评委会话可以在公开值保持 60 秒时验收五分钟，避免“必须先公开 300 才能验证 300”的循环依赖。最终发布 300 秒时，向 `PROVIDER_CONFORMANCE_REPORT_JSON` 注入 runner 生成的脱敏聚合报告，向 `PROVIDER_CANARY_RECEIPT_JSON` 注入 7 天内生成且绑定该报告 SHA-256、EvidenceReconciler 版本、主备模型、Prompt/ASR context 哈希、非秘密 Provider 配置摘要、FFmpeg 二进制／配置哈希、延迟指标和 Canary 结果的收据，并让 `DEPLOYMENT_COMMIT_SHA` 等于被验收镜像的完整提交 SHA；任一项不匹配时 `/ready` 失败关闭。

基础计划代表尚未选型的版本 B，因此备用路线和三个条件秘密保持关闭。生产同构评测必须先选出一个通过门槛的 Seed 主路线，并证明 Qwen 通过跨厂商备用门槛；若 Qwen 只适合成为主路线或没有 Seed 路线合格，本架构不得发布版本 C，需另立传输决策。版本 C 才把 `VISUAL_FALLBACK_ENABLED` 改为 `true` 并注入条件秘密。

截至 2026-07-23 的发布工作树实测事实是：线上 `trainpal-demo-009` 使用源码包构建，运行源与 B 的 `81de88d` 一致；295 秒、约 15.1 MiB 素材可完成请求，3 个评委并发成功且第 4 个返回 429，但 3 次公开分析的 coverage 均为 `partial`。这些只证明 300 秒 envelope 与并发保护，不证明五分钟完整质量达标。在本分支真实 Provider/COS 硬门槛全部通过前，结论是“不切线上”。

Qwen 备用路线要求 Bucket 私有、服务端加密、1 天生命周期和 10 分钟只读签名 URL。应用只为主路线失败的视觉块创建随机对象，重试复用同一对象，所有终态主动删除；清理失败后停止开放新的备用分析。

## B / C 发布顺序

1. 发布人先把真实环境 ID放入当前 PowerShell 进程的专用变量，不能写入仓库或回执：

   ```powershell
   $env:TRAINPAL_CLOUDBASE_ENV_ID = '<operator-supplied>'
   ```

2. 在私有/OA 访问状态下，以交互确认方式部署 Ark-only 版本 B：

   ```powershell
   $trainpalCommit = (git rev-parse HEAD).Trim()
   .\deploy\cloudbase\deploy-source.ps1 `
     -EnvironmentId $env:TRAINPAL_CLOUDBASE_ENV_ID `
     -CommitSha $trainpalCommit `
     -ServiceName trainpal-demo
   ```

   脚本不使用 `--force`，并在 CLI 交互页保留最终确认。交互页必须再次核对目标服务、访问方式和价格影响。源码构建日志通过后，先验证容器、签名 FFmpeg、`/health`、`/ready`、SSE、一条真实短视频和一条私有五分钟素材；版本 B 及其兼容环境配置必须保留。

3. Provider conformance 证明某条 Seed 路线可作为主路线且 Qwen 可作为跨厂商备用后，部署启用顺序降级的版本 C。若评测只支持 Qwen 作为主路线，本轮因“私有 COS 只中转失败块”的安全边界而阻断发布。版本 C 验证可降级错误、有效空结果不降级、熔断、取消以及私有 COS 主动删除；公共入口仍保持未宣布状态，`PUBLISHED_ANALYSIS_MAX_SECONDS` 仍为 60。

4. 全部私有门槛通过后执行短时未公布 Canary：第二浏览器可浏览；评委码真实五分钟分析成功；三个并发成功、第四个稳定 429；取消、熔断、COS 清理全部通过；实际完成 `B→C→B→C`。任一门槛失败就关闭入口，不发布 300 秒能力。

5. 把上述结果写成脱敏收据，包含布尔门禁、延迟数值、commit、精确模型 ID、Prompt/ASR context 哈希与非秘密 Provider 配置摘要（endpoint、ASR 权益、COS 范围和生命周期）。重新部署同一个版本 C 镜像，只更新 `PROVIDER_CANARY_RECEIPT_JSON`、`DEPLOYMENT_COMMIT_SHA` 与 `PUBLISHED_ANALYSIS_MAX_SECONDS=300`。确认 `/ready` 和 `/capabilities` 后才向队友宣布五分钟能力。

6. 仅在评委使用时把最小实例数设为 1，其余时间恢复 0。费用接近 300 元时先把真实分析并发归零；到最晚关闭时间禁用公共入口并缩容。

## 脱敏回执

只读脚本仅输出服务名、版本、Build ID、提交 SHA、资源规格、伸缩、访问类型和环境变量名称，不输出环境 ID、域名、Pod ID、日志正文或环境变量值：

```powershell
.\deploy\cloudbase\inspect-service.ps1 `
  -EnvironmentId $env:TRAINPAL_CLOUDBASE_ENV_ID `
  -CommitSha '<full-lowercase-git-sha>' `
  -OutputPath '<private-receipt-path>'
```

回执放在团队受控位置，不提交 Git。原视频、密钥、账户标识和可搜索的真实分析内容同样不得提交。
