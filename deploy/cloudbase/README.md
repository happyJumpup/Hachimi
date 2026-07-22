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
| 应用 / 平台请求超时 | 异步运行 180 秒 / HTTP 请求 60 秒 |
| 本地上传 | 后端合同：完整文件不超过 300 秒、256 MiB；CloudBase HTTP Access：20 MB；Web 安全上限：19,000,000 bytes |
| 视觉策略 | 60 秒分块、10 秒重叠、单块 20 秒、顺序执行 |
| 真实分析 | 匿名 0 并发；评委码 3 并发 |
| 总预算 | 300 元人民币硬上限 |
| 最晚关闭 | `2026-07-25T00:00:00+08:00` |

同一个容器提供 SPA、FastAPI 和 SSE。活跃运行保存在单进程内存中，因此最大实例数不能大于 1。默认域名只用于短期开发演示；首次访问可能出现腾讯云风险提示，评委说明应写明“首次打开会看到腾讯云安全提示，请点击‘确定访问’进入演示”。

## 当前脱敏验收快照

| 阶段 | 不可变记录 | 结论 |
| --- | --- | --- |
| 版本 A | `trainpal-demo-007` / Build `2601400378` / `e5ba966451300c8027f174229e902acc0dd4ba94` | OA 私有探针、签名 FFmpeg、短样本与 295 秒样本通过 |
| 版本 B | `trainpal-demo-008` / Build `2601400393` / `81de88d0b7475797a51a4fdf73d334d5836dd529` | 前端合并、私有真实短样本通过 |
| 回滚 | `008 → 007 → 008` | 三次 `/health`、`/ready` 均为 200 |
| 公网配置 | `trainpal-demo-009`，复用版本 B 同一镜像 | SPA/history、匿名隔离、三路 `202`、第四路 `429` 通过；三条完成结果均为 `partial` |

当前公网配置为 2 vCPU / 4 GiB、单 worker、最小/最大实例 `1/1`、`OA + PUBLIC`。已安排一次性任务在 2026-07-24 23:00（Asia/Shanghai）启动关停，确保本周六 0 点前禁用公网映射、恢复 `OA` 并把最小实例降为 0。该任务先用 CLI 刷新临时凭证，只在唯一环境和唯一目标路由核对成功时继续；不调用分析模型。

上述记录不包含环境 ID、域名、Pod、秘密值或真实分析内容。公网三路 `partial` 是真实质量限制，不能改写为 `complete`。

## 生产配置

秘密只通过 CloudBase 加密环境变量界面传入：

- `ARK_API_KEY`
- `VOLC_ASR_API_KEY`
- 新生成且彼此独立的 `JUDGE_ACCESS_CODE`
- 新生成的 `ACCESS_COOKIE_SECRET`

非秘密值以 `foundation-plan.json` 中的名称集合为准。关键值包括：

```text
APP_ENV=production
ANALYSIS_PROVIDER=cloud
LOCAL_UPLOAD_ENABLED=true
LOCAL_ANALYSIS_MAX_SECONDS=300
LOCAL_UPLOAD_MAX_BYTES=268435456
ANALYSIS_EVIDENCE_TIMEOUT_SECONDS=170
ANALYSIS_VISUAL_CHUNK_SECONDS=60
ANALYSIS_VISUAL_OVERLAP_SECONDS=10
ANALYSIS_CHUNK_TIMEOUT_SECONDS=20
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

CloudBase 官方源码部署要求代码目录根部包含 Dockerfile；CLI 使用 `--source` 上传该目录。仓库 `.dockerignore` 排除 Git、环境文件、媒体、测试产物、依赖目录和文档，任何真实视频都不能进入源码包。

Docker 多阶段构建从 FFmpeg 8.1.2 官方签名源码生成共享、LGPL-only 运行时。镜像内只允许 `/opt/trainpal/ffmpeg/bin/ffmpeg` 一份二进制，`/ready` 和镜像审计都校验同目录构建收据，并拒绝 GPL、nonfree、版本、配置或哈希不符。

本地门槛：

```powershell
.\deploy\cloudbase\preflight.ps1
docker build --tag trainpal-five-minute:local .
```

随后在 Bash 环境执行 `deploy/verify-competition-image.sh trainpal-five-minute:local`。通过项包括逐层无秘密/媒体扫描、唯一 FFmpeg 与收据、非 root 用户、容器启动、`/health`、SPA 根路由和 history fallback。

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

3. 前端提交后，重新生成 OpenAPI 类型、跑完整前端/E2E 和窄屏检查，再部署版本 B。源码包路线已验证；不再依赖失败的 GHCR 拉取路线。

4. 全部私有门槛通过后才打开短时公网 Canary：第二浏览器可浏览；评委码真实分析成功；三个并发成功、第四个稳定 429；终态无媒体残留；实际完成 B→A→B 回滚。任一门槛失败就关闭入口，不发布评委链接。

5. 仅在评委使用时把最小实例数设为 1，其余时间恢复 0。费用接近 300 元时先把真实分析并发归零；到最晚关闭时间禁用公共入口并缩容。

CloudBase HTTP Access 的平台限制是 20 MB 请求体和 60 秒单次 HTTP 请求。分析运行本身是异步的：multipart 创建必须在 60 秒内返回 `202`，随后客户端通过快照和 SSE 等待最长 180 秒的应用终态。约 24.9 MB 与 97 MB 请求在到达应用前被平台拒绝，约 15.1 MB 的 295 秒样本成功，因此前端采用 19 MB 安全边界。

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

`release-source.ps1` archives an exact clean Git commit, preserves production
secrets in memory, enforces the five-minute runtime profile, and submits an
OA-only gray source release. Its optional receipt contains names and hashes,
never environment values.

`private-canary.py` signs the private CloudBase HTTP API, reads the judge code
from Windows Credential Manager, verifies anonymous denial, uploads one local
video, consumes SSE, and emits only aggregate result counts and coverage state.

`public-canary.py` uses four independent browser-style sessions against an
operator-supplied HTTPS base URL, verifies anonymous denial, submits the same
authorized short sample concurrently, requires exactly three `202` responses
and one `429`, consumes all accepted SSE streams, and outputs aggregate terminal
and coverage states only. It never prints the judge code or analysis content:

```powershell
python .\deploy\cloudbase\public-canary.py `
  --public-base-url '<operator-supplied-https-origin>' `
  --media '<temporary-authorized-sample>' `
  --output '<private-receipt-path>'
```

回执放在团队受控位置，不提交 Git。原视频、密钥、账户标识和可搜索的真实分析内容同样不得提交。
