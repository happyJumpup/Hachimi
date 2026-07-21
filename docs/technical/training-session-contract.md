# 训练场次、本地媒体与本地数据合同

> 状态：下一轮本地视频原型已冻结；训练状态机继续沿用已验收基线
>
> 更新日期：2026-07-21

## 1. 边界与原则

训练执行器只消费用户已经确认的动作安排，不调用动作分析 Agent，也不评价动作顺序、训练量、伤病风险或训练效果。

- 当前设备同时最多存在一个未完成训练，固定存为 `sessions/current`。
- 用户导入的来源视频以 Blob 保存在同一浏览器数据库；它可以被方案、场次和记录引用，但不是训练记录本身，也不上传为服务端视频资产。
- 开始训练会深拷贝方案快照；之后编辑草稿或方案库不能改变正在训练或已经完成的记录。
- 活动训练只累计前台实际执行时间；组间休息按墙钟流逝，但计入时长最多不超过计划休息时间。
- 未完成训练可恢复，但不进入训练记录，也不产生最终卡路里或海报。
- 自建动作没有来源视频和演示片段，训练页显示无视频状态，不自动匹配替代视频。
- 本地来源媒体丢失时保留动作、参数和训练控制，并要求用户重新选择原视频；不得静默替换来源或阻止完成训练。
- Pet、卡路里、训练记录和海报都是训练执行器状态或终态记录的消费者，不能反向控制状态机。

## 2. Dexie v3

数据库名称继续使用 `hachimi-fitness`。v3 在现有 v2 六张训练表之外增加本地媒体表，不清除或重建已有数据：

```ts
db.version(3).stores({
  drafts: '&id, updatedAt, linkedPlanId',
  plans: '&id, updatedAt, createdAt, name',
  sessions: '&id, sessionId, status, updatedAt',
  records: '&id, endedAt, outcome',
  profiles: '&id, updatedAt',
  preferences: '&id, updatedAt',
  localMedia: '&sourceId, importedAt, updatedAt',
})
```

v1 → v2 的既有升级继续保留：迁移 `drafts` 并创建其余训练表。v2 → v3 只创建空的 `localMedia` 表；旧 `DraftSourceRef` 没有 `kind` 时按 `controlled` 读取，不批量重写历史记录。迁移中发生异常必须整体回滚并提示“本机训练数据暂时无法读取”，不得以清空数据库作为自动恢复。

### 2.1 草稿、方案、档案与偏好

现有 `DraftItem`、`SourcedValue<T>` 和字段来源规则保持不变。新增持久化类型：

```ts
interface DraftPlan {
  id: 'current'
  name: string
  linkedPlanId: string | null
  items: DraftItem[]
  updatedAt: string
}

interface SavedPlan {
  id: string
  name: string
  items: DraftItem[]
  createdAt: string
  updatedAt: string
}

interface TrainingProfile {
  id: 'current'
  sex: 'male' | 'female' | null
  age: number | null
  heightCm: number | null
  weightKg: number | null
  updatedAt: string
}

interface Preferences {
  id: 'current'
  petVisible: boolean
  updatedAt: string
}

interface LocalMediaFingerprint {
  fileName: string
  mimeType: string
  sizeBytes: number
  lastModified: number
  durationSeconds: number
}

interface LocalSourceMedia extends LocalMediaFingerprint {
  sourceId: `local:${string}`
  blob: Blob
  importedAt: string
  updatedAt: string
}

interface DraftSourceRef {
  sourceId: string
  title?: string
  originUrl?: string
  kind?: 'controlled' | 'local'
  localMedia?: LocalMediaFingerprint
}

type SegmentRole = 'follow_along' | 'teaching_demo' | 'unknown'

interface DraftItem {
  // 既有字段保持不变
  segmentRole?: SourcedValue<SegmentRole>
}
```

- 草稿继续 300ms 防抖自动保存。
- 打开方案库中的方案会在用户确认后用深拷贝替换当前草稿，并把 `linkedPlanId` 设为该方案 ID；后续自动保存以同一个 Dexie 事务同时更新草稿和原方案。
- “另存为”要求非空名称，创建新 UUID 方案，并把草稿链接到新方案；旧方案不变。开始训练本身不会把未链接草稿写入方案库。
- 快速体验方案是版本化前端 fixture。用户选择后只复制到当前草稿，除非主动“另存为”，否则不进入方案库。
- 删除一个方案不删除由它产生的训练场次或记录；若当前草稿仍链接该方案，删除事务同时把 `linkedPlanId` 置空，但保留草稿内容。
- 首次读取不到偏好时使用 `petVisible=true`。隐藏 Pet 立即写入长期偏好，不是当前场次临时状态。
- 本地导入生成规范小写 `local:<UUID>`，先完成能力、类型、大小和时长检查，再把 Blob 与指纹写入 `localMedia`。保存失败时可保留当前标签页内存 Blob，但不能写入“已保存到本机”状态。
- 本地视频动作的 `DraftSourceRef.kind='local'`，`sourceId` 等于 `LocalSourceMedia.sourceId`，并嵌入不含 Blob 的指纹；受控来源可显式写 `controlled`，旧数据缺少 `kind` 时仍按受控来源读取。
- 重新选择媒体时，用户选中的文件必须通过基本指纹与时长校验后才可替换相同 `sourceId` 的 Blob。替换媒体不改写方案、场次或记录快照。
- 新候选加入草稿时把 `segment_role` 写入可选 `DraftItem.segmentRole`：Agent 明确值使用 `video` 来源，用户确认或修改后使用 `user` 来源；该字段不使用 `rule`。旧草稿缺少字段时按 `unknown` 行为处理，不进行破坏性迁移。

### 2.2 展开式执行时间线

- 从来源视频得到的候选按绝对开始时间排序。循环或重复出现的动作不去重，每次出现都创建不同 ID 的扁平 `DraftItem`，例如 `A1 → B1 → A2 → B2`。
- 轮次只作为可选展示标签，不参与训练状态机。每个 `DraftItem.sets` 只表示该次动作安排内的连续组数，不能用 `sets=2` 把 `A → B` 的两轮折叠成 `A(2组) → B(2组)`。
- `segment_role='follow_along'` 时，明确的动作时长、休息和顺序可以写入 `video` 来源值；`teaching_demo` 的时间只写入演示片段，绝不自动写入训练时长，训练参数继续使用明确口令、规则默认值或用户值。
- `segment_role='unknown'` 且会改变参数时，加入草稿前要求一次确认。训练执行器只消费确认后的扁平安排，不在运行时重新调用 Agent 或推断段类型。

### 2.3 方案快照、训练场次与记录

```ts
interface PlanSnapshot {
  name: string
  source: 'draft' | 'saved' | 'sample'
  sourcePlanId: string | null
  items: DraftItem[]
}

type SessionStatus = 'active' | 'resting' | 'ready_to_continue' | 'paused'
type PauseReason = 'before_start' | 'user' | 'page_hidden' | 'recovered' | 'between_actions'

interface ActionProgress {
  itemId: string
  completedSets: number
  activeMilliseconds: number
  skipped: boolean
}

interface TrainingSession {
  id: 'current'
  sessionId: string
  revision: number
  status: SessionStatus
  pauseReason: PauseReason | null
  plan: PlanSnapshot
  currentItemIndex: number
  currentSetIndex: number
  currentSetActiveMilliseconds: number
  activeStartedAt: string | null
  restStartedAt: string | null
  restEndsAt: string | null
  scheduledRestSeconds: number | null
  creditedRestMilliseconds: number
  progress: ActionProgress[]
  petId: 'hachimi'
  startedAt: string
  updatedAt: string
}

interface CalorieEstimate {
  value: number
  method: 'personalized' | 'generic'
}

interface ActionResult {
  itemId: string
  name: string
  targetSets: number
  completedSets: number
  completedReps: number | null
  completedDurationSeconds: number | null
  activeSeconds: number
  status: 'completed' | 'partial' | 'skipped'
}

interface TrainingRecord {
  id: string // 与 sessionId 相同
  outcome: 'completed' | 'ended_early'
  plan: PlanSnapshot
  actions: ActionResult[]
  activeSeconds: number
  creditedRestSeconds: number
  trainingDurationSeconds: number
  completedActionCount: number
  calorie: CalorieEstimate
  petId: 'hachimi'
  startedAt: string
  endedAt: string
}
```

`currentItemIndex`、`currentSetIndex` 均从 0 开始，指向下一组要执行的动作和组。`progress` 按方案动作顺序初始化且一一对应。完成本组时先递增实际完成量并把索引推进到下一组或下一动作，再进入休息；因此 `resting` 和 `ready_to_continue` 中的索引始终指向休息后将要开始的目标。

训练记录不保存档案原值；它保存终态时算出的卡路里值及计算方式，因此用户以后修改档案不会改写历史。`trainingDurationSeconds` 固定为 `activeSeconds + creditedRestSeconds`，不采用开始至结束的墙钟差，避免离开页面或隔夜恢复夸大训练时长。

终态换算统一使用四舍五入到整秒。次数型的 `completedReps` 为目标次数乘已完成组数；时长型的 `completedDurationSeconds` 为目标时长乘已完成组数。未完成一整组的活动时间仍进入 `activeSeconds` 和卡路里，但不伪装为已完成次数或时长。`completedActionCount` 只统计 `status='completed'` 的动作。

`ActionResult.status` 由实际量确定：完成全部目标组为 `completed`；未完成全部目标组但至少完成一组为 `partial`；一组都未完成为 `skipped`。该派生规则同时适用于跳过剩余组和提前结束，不能由视图自行覆盖。

## 3. 开始训练与结构校验

“开始训练”只做结构校验，不做科学性或健康评估：

- 至少一个动作；动作 ID 唯一且名称非空。
- 组数为正整数；次数型必须有正整数次数且时长为空，时长型必须有正整数秒数且次数为空。
- 休息秒数为非负整数；重量为空或正数，且非空重量的来源必须为 `user`。
- 视频动作的 `sourceRef` 与有效演示片段同时存在；自建动作两者同时为空。

结构校验不要求本地媒体 Blob 此刻可读：Blob 缺失只影响演示播放，不能把已有视频动作改成自建动作或阻止训练。训练页应显示来源指纹和“重新选择原视频”，校验成功后恢复同一来源片段。

校验通过后，在一个 Dexie 写事务中使用固定主键 `current` 执行 `sessions.add`。若已经存在记录，返回 `active_session_exists`，界面只提供“继续训练”或“结束当前训练”，不能覆盖。新场次深拷贝方案，生成 `sessionId`，初始状态为 `paused/before_start`；用户再次点击“开始本组”后才进入 `active`。

训练安全提示与结构校验结果同时显示，但安全提示不要求额外确认，也不能阻止用户开始。

## 4. 状态机与命令

```mermaid
stateDiagram-v2
    [*] --> paused: 创建场次
    paused --> active: 开始或继续本组
    active --> paused: 用户暂停 / 页面隐藏 / 恢复归一化
    active --> resting: 本组完成且仍有后续
    active --> completed: 最后一组完成
    resting --> active: 用户提前继续
    resting --> ready_to_continue: 休息自然结束
    ready_to_continue --> active: 用户确认继续
    active --> paused: 跳过剩余组并移到下一动作
    active --> ended_early: 用户确认提前结束
    paused --> ended_early: 用户确认提前结束
    resting --> ended_early: 用户确认提前结束
    ready_to_continue --> ended_early: 用户确认提前结束
    completed --> [*]
    ended_early --> [*]
```

`completed` 和 `ended_early` 是终态事件，不留在 `sessions` 表：同一事务写入 `records/{sessionId}` 后删除 `sessions/current`。

### 4.1 次数型与时长型

- 次数型进入 `active` 后不自动计数。用户点击“完成本组”时，将目标次数作为该组实际完成次数，累计一组。
- 时长型进入 `active` 后按前台活动时间倒计时；达到目标秒数时自动完成本组。暂停、隐藏或刷新后不会在后台补算时长。
- 活动计时在内存使用单调时钟；每秒把增量提交到 `currentSetActiveMilliseconds`，并在命令、页面隐藏和卸载前立即提交。意外崩溃最多损失最近一次尚未提交的秒数，不能根据恢复时的墙钟补算。
- 完成一组后把本组活动时间移入对应 `ActionProgress.activeMilliseconds` 并清零本组计时。当前动作还有组或仍有下一个动作时，使用当前动作安排的休息值进入 `resting`；休息为 0 时直接进入 `ready_to_continue`。最后一个动作最后一组完成则终结场次。

### 4.2 休息、离开与恢复

- 进入休息时一次写入 `restStartedAt`、`restEndsAt` 和 `scheduledRestSeconds`。界面从墙钟推导剩余秒数，不每秒持久化休息倒计时。
- 用户提前点击“继续训练”时，计入从开始至点击的实际休息并直接进入 `active`。
- 自然结束时，计入时间上限为计划休息时长并进入 `ready_to_continue`；必须由用户确认才开始下一组。
- 休息期间离开训练路由，倒计时继续；全局“继续训练”入口显示剩余时间或“准备继续”。
- `active` 时页面变为 hidden 或离开训练路由，必须先结算前台增量再转为 `paused/page_hidden`。`paused` 和 `ready_to_continue` 都不累计任何时间。
- 应用启动或刷新读取到 `active` 时，不计算 `activeStartedAt` 之后的墙钟时间，直接归一化为 `paused/recovered`；读取到已过期的 `resting` 时，按计划上限结算并归一化为 `ready_to_continue`。

### 4.3 跳过与提前结束

- “跳过剩余组”先结算当前前台活动时间，再保留当前动作已经完成的组和全部活动时间，将其标记为 `partial`；若一组都未完成则为 `skipped`。未完成的次数或时长不计为完成量。
- 存在下一动作时，跳过后移动到下一动作并进入 `paused/between_actions`；用户明确开始后才执行。没有下一动作时，该命令按“提前结束训练”处理并要求确认。
- “提前结束训练”在任何非终态显示确认。确认后生成 `ended_early` 记录，只保存实际完成量；不生成训练海报。

## 5. 一致性与幂等

- 所有改变训练场次的命令携带调用方读到的 `revision`。Dexie 事务只在 revision 匹配时更新并递增；不匹配返回 `session_conflict`、重新加载最新场次并暂停当前页面，防止重复点击和多标签页静默覆盖。
- “完成本组”、自动计时完成、跳过和暂停都通过同一个训练 Repository 调用状态机，不允许视图直接写表。
- 终态事务先按 `sessionId` 检查记录：记录已存在时返回该记录；否则 `records.add` 后删除 `sessions/current`。重复完成、返回键重放或刷新不会生成第二条记录。
- 删除全部本机训练数据必须在一个事务中清空七张表，并要求用户明确确认；同时释放当前对象 URL。它不删除受控来源、活动中的服务端分析运行或已经在各终态清理的上传副本。

## 6. 领域事件与下游消费者

状态机命令同步返回以下确定性领域事件；竞赛版不持久化事件日志，也不通过网络发布：

```ts
type TrainingEvent =
  | { type: 'session.started'; sessionId: string }
  | { type: 'set.started'; itemId: string; setIndex: number }
  | { type: 'set.completed'; itemId: string; setIndex: number }
  | { type: 'session.paused'; reason: PauseReason }
  | { type: 'rest.started'; endsAt: string }
  | { type: 'rest.finished' }
  | { type: 'action.skipped'; itemId: string }
  | { type: 'session.completed'; recordId: string }
  | { type: 'session.ended_early'; recordId: string }
```

| 消费者 | 唯一输入 | 行为 |
| --- | --- | --- |
| Pet | 当前场次状态、暂停原因和终态事件 | `active→训练`、`resting→休息`、`paused/before_start` 或 `paused/between_actions→待机`、其他 `paused` 与 `ready_to_continue→暂停`、无场次→待机、完整完成结果页→完成 |
| 卡路里 | 终态实际活动/计入休息时间和当前档案 | 在记录事务中计算一次；Pet 和 Agent 不参与 |
| 训练记录 | 终态场次和方案快照 | 保存实际完成量；不引用可变方案 |
| 海报 | `outcome=completed` 的训练记录 | 即时生成；提前结束没有海报 |

Pet 加载失败、被隐藏或使用低动效都不改变状态机。Pet 可见性不影响完成海报：完整训练海报按产品合同始终包含哈肌咪。

## 7. 卡路里合同

GymBTI 的计算细节不属于本轮原型。本节既有卡路里规则继续作为当前训练结果基线，不因本地视频导入、Agent 分类或 Provider 路线变化而调整。

档案只有在性别、年龄、身高、体重四项均通过表单校验时才算完整：性别为 `male` 或 `female`，年龄为 18–100 的整数，身高为 100–250 cm、体重为 20–300 kg 的有限数值。不完整时不填入默认体重或虚构个人数据。

完整档案先计算 Mifflin–St Jeor 静息代谢率：

```text
RMR = 10 × weightKg + 6.25 × heightCm - 5 × age + sexOffset
sexOffset = 5 (male) 或 -161 (female)

personalizedKcal = round(
  RMR / 1440 × (3.5 × activeMinutes + 1.0 × creditedRestMinutes)
)
```

档案缺失任一项时采用弱化通用规则：

```text
genericKcal = round(4 × activeMinutes + 1 × creditedRestMinutes)
```

结果不得小于 0，只显示“约 N 千卡”和 `personalized`/`generic` 的非技术化说明，不显示区间或精确承诺。`creditedRestMinutes` 已按每段计划休息封顶；暂停、准备继续、后台停留和完成后的时间均不计入。训练记录保存值与方法，之后不重算历史。

## 8. 海报与隐私合同

只有 `outcome=completed` 的记录可以生成 1080×1920 PNG。海报只读取：

- 方案名称；
- `trainingDurationSeconds`；
- “约 N 千卡”；
- `completedActionCount`；
- 固定哈肌咪完成态素材。

海报不得出现动作/组数明细、训练档案、来源视频、原视频链接、AI 结论或数值置信度。优先使用 Web Share API 分享 PNG；不可用或被拒绝时提供下载。Canvas、字体或 Pet 素材加载失败时显示可重试错误，不生成缺字段或泄露内部信息的半成品。

## 9. 验收场景

- 从 v1 含跨视频和自建动作的 `current` 草稿升级，全部动作及字段来源保持不变。
- 从 v2 升级到 v3 后旧受控动作仍可训练；导入本地视频、刷新并复练时从同一 `local_source_id` 恢复 Blob 和片段。
- 本地 Blob 丢失时结构化方案仍可训练；重新选择正确文件恢复播放，错误文件不能静默替换。
- `A → B` 两轮来源按 `A1 → B1 → A2 → B2` 形成四个独立安排，完成、跳过和记录都按四个扁平条目推进。
- 草稿另存为、打开原方案继续编辑、再次另存为，三个副本互不串改。
- 次数型手动完成、时长型前台自动完成、计划休息自然结束和提前继续均产生正确实际时间。
- 活动训练切到后台、刷新和崩溃恢复后不会补算后台活动；休息恢复按计划上限进入准备继续。
- 连续双击完成、两个标签页同时操作、终态事务重试都只产生一次状态推进和一条记录。
- 跳过剩余组保留部分完成；提前结束生成记录但没有海报；完整结束生成可分享 PNG。
- 档案完整与不完整使用不同卡路里方法，修改档案后历史记录数值不变。
- Pet 隐藏、素材失败和低动效不影响任何训练命令。

## 10. 扩展边界

- 未来账号同步通过新的 Repository 与冲突协议实现，不能让当前本地表直接承担服务端同步队列。
- 未来抖音推荐页只消费 `resting`/`ready_to_continue` 的召回视图；离开训练页仍必须暂停活动训练。
- 自动计数、动作质量评判、健康风险判断、用户自定义 Pet 和训练处方不属于本状态机。
- GymBTI 细节、Pet 新能力和正式产品命名不属于本合同；没有新决策前保持现有卡路里与 Pet 五态。
- 新训练状态、跨设备并发或服务端记录都必须先更新本合同及 ADR-0010。

## 11. 关联决策

- [ADR-0001：Pet 是非阻塞的训练呈现层](../adr/0001-pet-is-a-non-blocking-presentation-layer.md)
- [ADR-0002：休息可以在推荐页中继续](../adr/0002-rest-can-continue-on-the-recommendation-page.md)
- [ADR-0003：训练场次可以恢复但不会在后台训练](../adr/0003-training-sessions-are-recoverable.md)
- [ADR-0004：分离草稿、方案库和训练记录](../adr/0004-separate-drafts-library-and-training-records.md)
- [ADR-0005：Agent 提出动作候选，用户决定训练方案](../adr/0005-agent-proposes-actions-users-decide-plans.md)
- [ADR-0010：训练数据留在同设备且仅有一个未完成场次](../adr/0010-local-training-data-and-single-active-session.md)
- [ADR-0013：本地视频优先与可恢复的覆盖分析](../adr/0013-local-video-import-and-recoverable-analysis.md)
