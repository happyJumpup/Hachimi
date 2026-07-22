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
| 本地上传 | 完整文件不超过 300 秒、256 MiB |
| 视觉策略 | 60 秒分块、10 秒重叠、单块 20 秒、顺序执行 |
| 真实分析 | 匿名 0 并发；评委码 3 并发 |
| 总预算 | 300 元人民币硬上限 |
| 最晚关闭 | `2026-07-25T00:00:00+08:00` |

同一个容器提供 SPA、FastAPI 和 SSE。活跃运行保存在单进程内存中，因此最大实例数不能大于 1。默认域名只用于短期开发演示；首次访问可能出现腾讯云风险提示，评委说明应写明“首次打开会看到腾讯云安全提示，请点击‘确定访问’进入演示”。

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

3. 两个前端任务提交后，重新生成 OpenAPI 类型、跑完整前端/E2E 和窄屏检查，使用 `--traffic` 部署版本 B。公共入口仍保持未宣布状态。

4. 全部私有门槛通过后才打开短时公网 Canary：第二浏览器可浏览；评委码真实分析成功；三个并发成功、第四个稳定 429；终态无媒体残留；实际完成 B→A→B 回滚。任一门槛失败就关闭入口，不发布评委链接。

5. 仅在评委使用时把最小实例数设为 1，其余时间恢复 0。费用接近 300 元时先把真实分析并发归零；到最晚关闭时间禁用公共入口并缩容。

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

回执放在团队受控位置，不提交 Git。原视频、密钥、账户标识和可搜索的真实分析内容同样不得提交。
