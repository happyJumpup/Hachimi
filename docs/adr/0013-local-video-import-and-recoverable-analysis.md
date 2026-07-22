# 0013：本地视频优先与可恢复的覆盖分析

状态：已接受（2026-07-21）；第 7 项及公开 `segment_role` 合同由 ADR-0017 取代

> 2026-07-23 更新：下文“今晚 60 秒”、单次全范围 Provider、`partial` 尚未生产验收和 GYMTI 暂缓均为当时的历史边界。当前竞赛能力由 [ADR-0030](0030-five-minute-chunked-analysis-and-signed-ffmpeg.md) 更新为完整源文件最多 300 秒的分块路线；GYMTI v1 由 [ADR-0031 至 ADR-0042](0031-questionnaire-recommends-main-app-confirms-coach-style.md) 更新。其余本地优先、显式触发、恢复、覆盖缺口和清理原则继续有效。

## 背景

竞赛版用不超过 60 秒的团队受控视频证明了完整动作分析链路，但它不能验证用户把自己已经找到的视频转成训练的核心价值。下一轮原型需要把本地视频导入变成主入口，同时诚实处理长视频耗时、页面离开、局部失败和循环跟练内容。

本决策只冻结产品与公共边界。原生音视频 benchmark 已在本地完成既定门禁，但没有产生 Seed／Qwen 质量冠军，也没有验证长视频生产路线；因此不在这里切换 Provider，也不把未验证的 10 分钟目标写成今晚已经具备的能力。

## 决策

1. 产品继续采用独立 Web 形态。本地视频导入是主入口；受控视频源只作为快速体验和浏览器本地文件能力不可用时的兜底。任意 URL 抓取、平台 Cookie、登录态复用和信息流爬取仍不接受。
2. 浏览器在当前设备持久保存用户导入的原视频与稳定 `local_source_id`，供刷新恢复、方案编辑、训练和复练播放。服务端每次只接收分析所需的临时副本，并在成功、部分完成、失败或取消后清除；不建设云端媒体库，也不承诺跨设备找回。浏览器失去媒体时必须要求用户重新选择并校验来源，不能静默换片。
3. 产品目标边界是完整覆盖不超过 10 分钟的来源视频；具体部署的真实上限由 `GET /api/v1/capabilities` 返回。今晚原型默认上限为已经验证的 60 秒，界面在选片和触发前展示该实际限制。未通过完整分块、恢复和清理验证前，不得把 10 分钟目标宣传成已支持能力。
4. 分析只由用户点击“分析视频动作”显式创建。页面切换、刷新或 SSE 短暂断线不取消运行；客户端保存运行标识，通过快照和事件流恢复。用户主动取消、更换来源或触发部署配置的安全边界才终止运行。运行仍是同设备、短时、可过期的任务，不扩展为跨设备或永久后台队列。
5. 过程反馈只展示真实阶段、`processed_seconds / source_duration_seconds` 和暂时发现的动作线索数。非终态的 `discovered_candidate_count` 是已完成证据分支返回的保守线索数，允许在融合时收敛；可靠终态才表示融合候选数。中间线索只读，不能编辑或加入方案。界面不展示模型思维过程、虚假百分比、未经实测的预计时间或倒计时承诺。
6. 系统或 Provider 无法判断的时间范围必须成为明确的分析覆盖缺口。一次运行可以以 `coverage_status=partial` 返回可靠候选和 `coverage_gaps`；缺口只暴露绝对范围、是否可重试和版本化安全原因码，不透出 Provider 正文，也不伪装成动作行。已完成部分仍可校正和使用，但不得称为完整分析。用户可以仅重试缺口区间：基础方案尚未确认且没有被修改时，成功结果按绝对来源时间生成更新版本的整份提案；方案已经确认或编辑后，重试或迟到结果不能自动插入，重新分析必须形成新提案，由用户决定追加或替换。失败时保留缺口和重试入口。正常分析后没有动作证据的区间不是覆盖缺口；`coverage_status=insufficient` 不生成空方案，只提供重试和手工创建动作。
7. 动作分析 Agent 必须区分跟练执行段与教学演示段。跟练执行段的顺序、时长和休息可以作为视频来源默认值；教学演示段只提供动作预览和出处，不能直接把演示时长当训练时长。不能可靠区分且会影响参数时，只发起一次必要确认。
8. 循环内容进入方案时统一转换为展开式执行时间线，例如 `A1 → B1 → A2 → B2`。每次出现形成独立、扁平的动作安排；轮次只作标签，组数不代替轮次，不引入嵌套循环状态机。
9. GymBTI 的计算细节、正式产品名和 Pet 新能力暂缓。现有 production Provider 继续作为默认，但当前仍是单次全范围路径，只能可靠返回完整覆盖或整体失败，不能可靠定位可重试的 `partial` 时间缺口。`partial`／缺口重试先由公共合同和可注入测试 Provider 验证，不得写成真实 Provider 已交付；它仍是后续必须补齐的生产验收目标。已完成的 native audio/video benchmark 不足以选出质量冠军，未来切换必须补齐长视频证据、复核结果并另行记录路线决策。

## 公共接口影响

- 新增 `GET /api/v1/capabilities`，向客户端暴露当前部署真实支持的本地导入与时长边界。
- 新增 `POST /api/v1/analysis-runs/local`，使用 `multipart/form-data` 接收 `media`、`local_source_id`，以及成对可选的 `range_start_seconds`、`range_end_seconds`。
- `AnalysisRunView` 增加 `source_duration_seconds`、`processed_seconds`、`discovered_candidate_count`、`coverage_status` 和 `coverage_gaps`；`AnalysisCandidate` 增加 `segment_role=follow_along | teaching_demo | unknown`。
- 受控来源的 `POST /api/v1/analysis-runs` 与读取、事件、取消接口继续存在；两类来源共用相同的运行快照、恢复、覆盖和清理语义。

## 取代关系

本 ADR 只取代以下冲突部分：

- [ADR-0007](0007-web-first-controlled-video-sources.md) 中“浏览器只能提交受控 `source_id`”以及受控视频作为唯一入口的边界；Web-first、禁止任意 URL 和平台适配隔离仍有效。
- [ADR-0008](0008-explicit-transient-analysis-pipeline.md) 中“离开入口立即失效”和运行只存在于页面连接生命周期的边界；显式编排、测试假实现隔离和临时材料清理仍有效。
- [ADR-0012](0012-explicit-full-source-analysis-with-latency-budget.md) 中 60 秒产品上限、离页或 SSE 断开即取消，以及把单一 Provider 时延预算当作下一轮产品承诺的部分；显式触发、完整覆盖、绝对时间和不以稀疏抽样冒充完整分析仍有效。

## 结果

- 原型可以验证真实用户从自己拥有的视频到可执行训练的主路径，同时保留无需上传等待的受控快速体验。
- 原视频与训练数据保持同设备边界；服务端只承担可清理的分析副本和短时运行状态。
- 按该合同完成真实 Provider 路线后，局部失败不会抹掉可靠结果，也不能被伪装成“没有动作”；重试成本限定在明确缺口。当前只完成公共合同与测试 Provider 证明，尚未通过这项生产验收。
- 10 分钟是待验证的产品目标，部署能力以公共接口和实测为准，今晚不会夸大现状。

## 关联文档

- [本地视频训练原型规格](../specs/local-video-training-prototype.md)
- [Web 技术架构合同](../technical/web-mvp-architecture.md)
- [Web 体验设计规范](../design/web-experience-guidelines.md)
- [训练场次、本地媒体与本地数据合同](../technical/training-session-contract.md)
