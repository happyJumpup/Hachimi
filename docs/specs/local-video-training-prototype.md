# 本地视频训练原型规格

Status: frozen product contract; implementation acceptance pending

Updated: 2026-07-21

Audience: 产品、设计、Web/API 开发、测试与演示人员

本文件是下一轮原型的权威规格。[竞赛 Web MVP 规格](competition-web-mvp.md)继续记录已完成的受控视频竞赛基线，但其中“受控视频是唯一入口”“离页即取消”和“60 秒是产品上限”的内容不再适用于本原型。

## 1. 目标

验证用户能否把当前设备上自己有权使用的健身视频，转成可确认、可编排、可执行并可复练的一次训练：

`导入本地视频 → 显式触发完整分析 → 查看真实进度 → 校正动作与来源时间线 → 编排方案 → 训练与复练`

原型继续使用一个任务型动作分析 Agent。它提出动作候选、识别来源节奏并区分跟练执行与教学演示；用户决定动作、参数、顺序和是否保存或训练。产品不读取平台 Cookie、不解析任意分享链接，也不把视频自动上传成云端资产。

## 2. 入口与本地媒体

### 2.1 主入口

- 首页首先提供“选择本地视频”，接受当前部署声明的格式、大小与时长范围。
- 受控视频源收纳在明确标注的“快速体验”区域，用于无需选择文件的演示和本地导入不可用时的兜底；它不能冒充用户导入或真实分析回退。
- 页面加载、选择文件、播放、暂停、拖动和返回页面都不自动发起分析。只有“分析视频动作”创建请求。
- 用户确认自己有权使用所选媒体；首版不增加平台授权抓取、Cookie 下载或任意 URL 输入框。

### 2.2 同设备保存

- 导入成功后，浏览器以 `local:<lowercase UUID>` 生成稳定 `local_source_id`，并把原始 Blob、文件名、MIME、字节数、`lastModified`、媒体时长和导入时间保存在 IndexedDB。
- 同一个 `local_source_id` 被动作候选、方案快照和训练记录引用。刷新、进入方案、稍后训练和复练都从该本地媒体记录播放，不生成裁剪副本。
- IndexedDB 写入失败时可以在当前页面内继续使用内存对象 URL，但必须显示“仅本次打开可用”；不得声称已经保存。
- 浏览器清除站点数据、无痕模式、配额回收或文件记录损坏都可能使媒体失效。用户可以重新选择文件；客户端以文件名、类型、大小、`lastModified` 和时长做基本校验后，将 Blob 重新绑定到原 `local_source_id`。不匹配时要求再次确认，不能静默换片。
- “清除本机训练数据”同时清除本地媒体 Blob、草稿、方案、场次、记录、档案和偏好；不会调用云端视频删除，因为服务端不长期保存来源媒体。

## 3. 能力边界必须诚实

- 产品目标是完整覆盖不超过 10 分钟的视频；既有 benchmark 已完成本地门禁，但没有验证长视频生产路线，分块、恢复和清理仍待补证。这是目标，不是当前部署承诺。
- 客户端每次进入页面都读取 `GET /api/v1/capabilities`，以 `local_upload_enabled`、`local_analysis_max_seconds` 和 `local_upload_max_bytes` 作为实际门禁。
- 今晚原型的安全默认值是 60 秒。只有真实技术路线测试和端到端验收通过后，部署配置才能提高；界面永远显示接口返回的当前上限，不硬写“支持 10 分钟”。
- 文件超出当前大小或时长上限时，在上传前说明实际限制并保留重新选择、使用受控视频或自建动作入口。客户端校验只用于及时反馈，服务端仍独立校验。
- `local_upload_enabled=false` 时隐藏或禁用上传主操作并解释当前环境不支持，受控快速体验仍可用。

## 4. 分析运行与恢复

### 4.1 生命周期

- 完整分析或覆盖缺口重试都会创建新的 Analysis Run。客户端保存当前来源对应的 `run_id`，并以服务端快照为权威状态。
- 页面切换、组件卸载、刷新或 SSE 短暂断线不发送取消。返回后先读取 `GET /api/v1/analysis-runs/{run_id}`，若仍在运行则重连事件流；客户端只应用当前来源与 `run_id` 的事件，并对重复 `sequence` 保持幂等。终态则直接恢复结果。
- 用户显式点击“取消”或确认更换来源时才调用 `DELETE /api/v1/analysis-runs/{run_id}`。取消、来源代次变化和运行过期后，迟到事件不得覆盖当前页面。
- 可恢复只指同设备短时恢复。服务重启、任务 TTL 到期或浏览器丢失运行标识后可以明确失败并允许重新上传重试；不承诺跨设备或永久后台任务。
- 服务端在每个运行的成功、部分完成、失败和取消终态清除上传媒体、音频、帧、转录与模型原始响应。恢复依赖运行快照，不依赖保留媒体副本。

### 4.2 真实过程预览

- 界面展示服务端真实 `stage`、已处理时长和暂时发现的动作线索数量，例如“已处理 00:42 / 01:00，暂时发现 3 条动作线索”。
- 对完整分析，进度分母是 `source_duration_seconds`；对区间重试，分母是 `range_end_seconds - range_start_seconds`，同时标明正在重试的绝对区间。`processed_seconds` 表示本次运行已处理的时长，不是预计完成度。
- `discovered_candidate_count` 在排队／运行阶段只驱动“暂时发现 N 条动作线索”，表示已完成证据分支返回的保守线索数，不等于可编辑候选，允许在终态融合时收敛；进入可靠终态后才表示融合候选数。中间线索只读，不能预览、编辑或加入方案。
- 不显示模型思维过程、供应商名称、置信度、虚假百分比、未经测量的预计完成时间或固定倒计时。

### 4.3 结果与覆盖

| 结果 | 判定 | 用户操作 |
| --- | --- | --- |
| 完整结果 | 本次范围 `coverage_status=complete` 且无覆盖缺口 | 校正、选择、加入方案 |
| 部分结果 | 完整来源仍有 `coverage_gaps` | 使用可靠候选；逐个重试缺口；手工补充 |
| 空结果 | 完整覆盖成功但没有动作证据 | 重试、创建动作、换视频 |
| 系统失败 | 没有足以安全返回的覆盖结果 | 重试、创建动作、换视频；不伪装为空结果 |
| 已取消／已过期 | 请求不再继续 | 重新创建请求 |

- `coverage_status` 描述单个运行所请求范围的覆盖。区间重试成功返回该区间的 `complete`；客户端再把新候选和既有来源级结果按绝对时间合并，移除被覆盖的缺口。
- 覆盖缺口只表示系统或 Provider 未能判断的区间。正常完成但没有动作证据的区间不生成缺口。
- 每个部分结果缺口必须保留绝对开始／结束时间和单独“重试这段”入口。当前产品要求缺口可重试；一次重试失败不会删除原可靠候选或缺口。
- 合并以 `local_source_id` 和来源绝对时间为边界；新运行只能替换它实际覆盖的缺口，不能覆盖用户已经校正的其他候选。候选冲突时保留用户修改并要求确认，不静默去重。
- 当前 production Provider 仍按单次全范围执行，只能可靠返回完整覆盖或整体失败，尚不能可靠定位可重试的时间缺口。`partial`／`coverage_gaps`／逐缺口重试目前只完成公共合同、客户端合并路径和可注入测试 Provider 验证；这不表示真实 Provider 路线已经交付。该能力保留为后续必须补齐的生产验收目标。

## 5. 来源时间线与训练映射

- Agent 按来源绝对时间返回动作、休息和切换的顺序；候选按开始时间排序，同一动作多次出现时不折叠成动作目录。
- 每个 `AnalysisCandidate.segment_role` 为 `follow_along | teaching_demo | unknown`。只有一侧提供明确角色时保留该角色；两侧都明确且一致时保留共同角色，冲突或都未知时写 `unknown`。`unknown` 和只有单一证据分支的候选必须 `needs_confirmation=true`。
- `follow_along` 表示创作者明确邀请同步完成的跟练执行段。其动作持续时间、休息和顺序可以成为 `video` 来源默认值。
- `teaching_demo` 表示教学演示段，只用于预览和找回出处。演示持续时间绝不自动写入 `parameters.duration_seconds`；参数只来自视频明确口令、规则默认值或用户修改。
- `unknown` 会改变清单或训练参数时，只向用户提出一次必要确认；不确定结果不得伪装成个性化处方。
- 循环内容以展开式执行时间线进入方案，例如 `A1 → B1 → A2 → B2`。每次出现是独立的扁平 `DraftItem`，保留自己的来源片段和参数；轮次可作标签，但不建立嵌套循环状态机，也不用组数代替轮次。
- 今晚纵切片可以直接按候选绝对时间展开重复动作，不要求新增独立时间线编辑器；后续专门视图仍必须遵守上述模型。

## 6. 公共 HTTP 合同

### 6.1 能力

`GET /api/v1/capabilities` 返回：

```ts
interface CapabilitiesView {
  local_upload_enabled: boolean
  local_analysis_max_seconds: number
  local_upload_max_bytes: number
}
```

该响应不包含 Provider、密钥、内部模型 ID 或未经验证的未来上限。
`local_analysis_max_seconds` 必须在 `(0, 600]` 内，`local_upload_max_bytes` 必须为正整数；功能关闭时仍返回当前配置边界，客户端以 `local_upload_enabled` 决定是否允许创建。

### 6.2 创建本地分析

`POST /api/v1/analysis-runs/local` 使用 `multipart/form-data`：

| 字段 | 要求 |
| --- | --- |
| `media` | 必填文件；支持 `video/mp4`、`video/quicktime`、`video/webm` |
| `local_source_id` | 必填；规范小写 `local:<UUID>`，不是路径或文件名 |
| `range_start_seconds` | 与 `range_end_seconds` 成对可选，`>= 0` |
| `range_end_seconds` | 与 `range_start_seconds` 成对可选，`> start` 且不超过来源时长 |

不提供区间时分析完整来源；提供区间时只分析该绝对范围，但仍上传浏览器保存的原文件。成功创建返回 `202` 和与受控来源相同的 `AnalysisRunView`。区间结果中的候选与证据时间都保持原视频绝对秒数。

本地入口的公开错误包括：可信代理客户端地址无效 `400`、会话／同源校验失败 `403`、功能关闭 `404`、文件过大 `413`、不支持的媒体类型 `415`、ID／媒体时长／区间无效 `422`、容量 `429` 和就绪 `503`。错误响应不暴露本机路径、Provider 正文或内部请求数据。

### 6.3 运行视图

`AnalysisRunView` 在现有字段上增加：

```ts
type CoverageGapReason =
  | 'provider_error'
  | 'timeout'
  | 'media_error'
  | 'unknown'

interface CoverageGap {
  start_seconds: number
  end_seconds: number
  reason: CoverageGapReason
  retryable: boolean
}

interface AnalysisRunView {
  // 现有字段保持不变
  source_duration_seconds: number
  processed_seconds: number
  discovered_candidate_count: number
  coverage_status: 'complete' | 'partial' | null
  coverage_gaps: CoverageGap[]
}
```

- `AnalysisCandidate` 增加必填 `segment_role: 'follow_along' | 'teaching_demo' | 'unknown'`；没有明确角色或明确证据冲突时使用 `unknown`，不得猜测角色。
- 排队、运行、失败和取消时 `coverage_status=null`；可靠终态才为 `complete` 或 `partial`。
- `source_duration_seconds` 是有限正数且始终表示原视频完整时长；`processed_seconds` 是有限非负数，区间运行时最大为区间长度；`discovered_candidate_count` 是非负整数，非终态为动作线索数，可靠终态为融合候选数。
- `coverage_gaps` 必须位于来源时长内、开始早于结束、按时间排序且互不重叠。
- SSE 包络 `{sequence,type,run_id,timestamp,data}` 不变，每个事件的 `data` 携带最新进度／覆盖快照；断线不触发服务端取消。
- `reason` 是独立、版本化的公开安全枚举，只供客户端映射自然文案；非白名单值不能进入 GET／SSE，Provider 正文不得透出。当前部分结果中的每个缺口都必须可单独重试。

现有接口继续保留：受控来源创建 `POST /api/v1/analysis-runs`、快照 `GET /api/v1/analysis-runs/{run_id}`、事件 `GET /api/v1/analysis-runs/{run_id}/events` 和显式取消 `DELETE /api/v1/analysis-runs/{run_id}`。

## 7. 原型验收

1. 导入一条能力范围内的本地视频，刷新后仍可播放，`local_source_id` 不变；服务端分析结束后不保留上传副本。
2. 超过当前时长或大小上限时，上传前后都被一致拒绝，并展示接口返回的真实限制；界面不宣称已经支持 10 分钟。
3. 点击分析后切到方案页、刷新或断开 SSE，再返回时恢复同一 `run_id`、真实阶段和终态；这些动作不触发 `DELETE`。
4. 过程预览随服务端快照推进，中间动作线索不可编辑或加入方案，终态后才开放融合候选校正。
5. **待生产路线补证：** 构造中间一分钟失败的部分结果：两侧可靠候选可使用，缺口范围清晰；只重试该区间成功后按绝对时间合并并移除缺口。公共合同和测试 Provider 通过不能替代真实 production Provider 的这项验收。
6. 空结果、系统失败、部分结果、取消、容量繁忙和本地媒体丢失具有不同文案与操作，不用预置候选回填。
7. 单侧明确角色可保留，双侧明确角色一致时保留、冲突时返回 `unknown`；`unknown` 和单分支候选要求确认，教学演示时长不进入训练时长。`A → B` 循环两轮进入方案后顺序为 `A1 → B1 → A2 → B2`，没有嵌套循环对象。
8. 本地媒体丢失时可重新选择并校验后恢复播放；拒绝错误文件后，训练控制和已有结构化方案仍可使用。
9. 受控快速体验继续完成原有训练闭环，且视觉上明确不是用户本地导入或 AI 失败回退。

## 8. 暂缓与决策门

- 既有 native audio/video benchmark 没有产生 Seed／Qwen 质量冠军，也没有验证 10 分钟生产路线；必须补齐长视频分块覆盖、局部重试、临时材料清理和目标设备验收后才能提高能力值。
- 当前生产 Provider 保持不变；任何切换都需要复核后的长视频证据和新的技术路线决策，不能把既有 benchmark 表述成 Seed 或 Qwen 胜出。
- GymBTI 的计算细节、正式产品名和 Pet 新能力暂缓；现有训练 Pet 五态和非阻塞边界保持不变。
- 抖音小程序、平台登录态导入、任意 URL 抓取、账号、跨设备媒体同步、服务端视频库和开放式 AI 私教不属于本原型。

## 9. 关联文档

- [领域词汇](../../CONTEXT.md)
- [ADR-0013：本地视频优先与可恢复的覆盖分析](../adr/0013-local-video-import-and-recoverable-analysis.md)
- [Web 技术架构合同](../technical/web-mvp-architecture.md)
- [Web 体验设计规范](../design/web-experience-guidelines.md)
- [训练场次、本地媒体与本地数据合同](../technical/training-session-contract.md)
