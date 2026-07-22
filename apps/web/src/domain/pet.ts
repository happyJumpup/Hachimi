export const PET_IDS = [
  'hotblood',
  'gentle',
  'snarky',
  'analyst',
  'comedian',
  'challenger',
  'zen',
] as const

export type PetId = (typeof PET_IDS)[number]

export const PET_CUES = [
  'idle',
  'encourage',
  'success',
  'caution',
  'remind',
  'report',
  'countdown',
  'rest',
  'complete',
  'drag',
] as const

export type PetCue = (typeof PET_CUES)[number]

export const ALL_PET_ACTIONS = [
  'idle',
  'cheer',
  'hop',
  'drag',
  'comfort',
  'guide',
  'rest',
  'mock',
  'point',
  'annoyed',
  'scan',
  'analyze',
  'report',
  'talk',
  'laugh',
  'celebrate',
  'challenge',
  'countdown',
  'power-up',
  'breathe',
  'nod',
  'relax',
] as const

export type PetAction = (typeof ALL_PET_ACTIONS)[number]

export interface PetActionDefinition {
  frameCount: number
  durationMs: number
}

export interface PetDefinition {
  id: PetId
  displayName: string
  shortName: string
  personality: string
  actions: readonly PetAction[]
  actionDefinitions: Partial<Record<PetAction, PetActionDefinition>>
  cueMap: Record<PetCue, PetAction>
}

const SIX_FRAMES = 6

function action(frameCount: number, durationMs: number): PetActionDefinition {
  return { frameCount, durationMs }
}

export const PET_ACTION_LABELS: Record<PetAction, string> = {
  idle: '待机',
  cheer: '加油',
  hop: '庆祝',
  drag: '拖动',
  comfort: '陪伴',
  guide: '引导',
  rest: '休息',
  mock: '吐槽',
  point: '督促',
  annoyed: '不耐烦',
  scan: '扫描',
  analyze: '分析',
  report: '播报',
  talk: '话痨',
  laugh: '大笑',
  celebrate: '欢庆',
  challenge: '挑战',
  countdown: '倒计时',
  'power-up': '爆发',
  breathe: '呼吸',
  nod: '点头',
  relax: '放松',
}

export const PET_ACTION_DESCRIPTIONS: Record<PetAction, string> = {
  idle: '保持角色个性的轻微呼吸与眨眼循环。',
  cheer: '挥拳鼓劲，适合训练开始或完成一组。',
  hop: '轻快跳起，适合达成目标或训练完成。',
  drag: '被用户拖动时使用的拿起与恢复反馈。',
  comfort: '温柔靠近并给予陪伴和鼓励。',
  guide: '用清楚而柔和的动作提示用户调整。',
  rest: '安静坐下或舒展，适合组间恢复。',
  mock: '带一点嫌弃地吐槽，但仍在督促。',
  point: '明确指出下一步，催促用户继续。',
  annoyed: '抱臂或轻轻跺脚，表达不耐烦。',
  scan: '启动传感器检查动作和身体状态。',
  analyze: '专注处理数据并形成判断。',
  report: '冷静给出分析结果和完成反馈。',
  talk: '夸张而连续地讲解和鼓励。',
  laugh: '被训练表现逗乐的开怀反馈。',
  celebrate: '用幽默夸张的方式庆祝完成。',
  challenge: '强硬发起下一轮挑战。',
  countdown: '进入最后冲刺的紧张倒数状态。',
  'power-up': '蓄力爆发，适合突破或完成目标。',
  breathe: '稳定呼吸，帮助用户放慢节奏。',
  nod: '松弛地点头认可当前进度。',
  relax: '完全放松下来，适合休息和恢复。',
}

export const PET_REGISTRY: Record<PetId, PetDefinition> = {
  hotblood: {
    id: 'hotblood',
    displayName: '热血鼓励型',
    shortName: '热血猫',
    personality: '积极、兴奋、鼓劲、突破自己',
    actions: ['idle', 'cheer', 'hop', 'drag'],
    actionDefinitions: {
      idle: action(SIX_FRAMES, 180),
      cheer: action(SIX_FRAMES, 120),
      hop: action(SIX_FRAMES, 130),
      drag: action(SIX_FRAMES, 150),
    },
    cueMap: {
      idle: 'idle', encourage: 'cheer', success: 'hop', caution: 'idle',
      remind: 'cheer', report: 'idle', countdown: 'cheer', rest: 'idle',
      complete: 'hop', drag: 'drag',
    },
  },
  gentle: {
    id: 'gentle',
    displayName: '温柔陪伴型',
    shortName: '温柔猫',
    personality: '温柔、安全、照顾身体状态',
    actions: ['idle', 'comfort', 'guide', 'rest'],
    actionDefinitions: {
      idle: action(SIX_FRAMES, 190),
      comfort: action(SIX_FRAMES, 145),
      guide: action(SIX_FRAMES, 155),
      rest: action(SIX_FRAMES, 190),
    },
    cueMap: {
      idle: 'idle', encourage: 'comfort', success: 'comfort', caution: 'guide',
      remind: 'guide', report: 'guide', countdown: 'comfort', rest: 'rest',
      complete: 'comfort', drag: 'rest',
    },
  },
  snarky: {
    id: 'snarky',
    displayName: '毒舌督促型',
    shortName: '毒舌猫',
    personality: '嫌弃、吐槽、但仍然认真督促',
    actions: ['idle', 'mock', 'point', 'annoyed'],
    actionDefinitions: {
      idle: action(SIX_FRAMES, 180),
      mock: action(SIX_FRAMES, 145),
      point: action(SIX_FRAMES, 135),
      annoyed: action(SIX_FRAMES, 155),
    },
    cueMap: {
      idle: 'idle', encourage: 'point', success: 'point', caution: 'annoyed',
      remind: 'mock', report: 'point', countdown: 'annoyed', rest: 'idle',
      complete: 'point', drag: 'annoyed',
    },
  },
  analyst: {
    id: 'analyst',
    displayName: '专业数据型',
    shortName: '专业猫',
    personality: '理性、精准、擅长数据分析',
    actions: ['idle', 'scan', 'analyze', 'report'],
    actionDefinitions: {
      idle: action(SIX_FRAMES, 185),
      scan: action(SIX_FRAMES, 140),
      analyze: action(SIX_FRAMES, 150),
      report: action(SIX_FRAMES, 145),
    },
    cueMap: {
      idle: 'idle', encourage: 'report', success: 'report', caution: 'scan',
      remind: 'analyze', report: 'report', countdown: 'scan', rest: 'idle',
      complete: 'report', drag: 'idle',
    },
  },
  comedian: {
    id: 'comedian',
    displayName: '幽默话痨型',
    shortName: '话痨猫',
    personality: '话多、幽默、反馈夸张',
    actions: ['idle', 'talk', 'laugh', 'celebrate'],
    actionDefinitions: {
      idle: action(SIX_FRAMES, 175),
      talk: action(SIX_FRAMES, 130),
      laugh: action(SIX_FRAMES, 125),
      celebrate: action(SIX_FRAMES, 120),
    },
    cueMap: {
      idle: 'idle', encourage: 'talk', success: 'laugh', caution: 'talk',
      remind: 'talk', report: 'talk', countdown: 'talk', rest: 'idle',
      complete: 'celebrate', drag: 'laugh',
    },
  },
  challenger: {
    id: 'challenger',
    displayName: '挑战突破型',
    shortName: '挑战猫',
    personality: '强硬、冲刺、挑战极限',
    actions: ['idle', 'challenge', 'countdown', 'power-up'],
    actionDefinitions: {
      idle: action(SIX_FRAMES, 170),
      challenge: action(SIX_FRAMES, 125),
      countdown: action(SIX_FRAMES, 135),
      'power-up': action(SIX_FRAMES, 115),
    },
    cueMap: {
      idle: 'idle', encourage: 'challenge', success: 'power-up', caution: 'challenge',
      remind: 'challenge', report: 'challenge', countdown: 'countdown', rest: 'idle',
      complete: 'power-up', drag: 'power-up',
    },
  },
  zen: {
    id: 'zen',
    displayName: '佛系陪练型',
    shortName: '佛系猫',
    personality: '松弛、接纳、慢慢陪伴',
    actions: ['idle', 'breathe', 'nod', 'relax'],
    actionDefinitions: {
      idle: action(SIX_FRAMES, 200),
      breathe: action(SIX_FRAMES, 210),
      nod: action(SIX_FRAMES, 165),
      relax: action(SIX_FRAMES, 220),
    },
    cueMap: {
      idle: 'idle', encourage: 'nod', success: 'nod', caution: 'breathe',
      remind: 'nod', report: 'nod', countdown: 'breathe', rest: 'relax',
      complete: 'relax', drag: 'relax',
    },
  },
}

// Kept for existing hotblood-only call sites while the product migrates to the registry API.
export const PET_ACTIONS = PET_REGISTRY.hotblood.actions
export const PET_FRAME_COUNT = SIX_FRAMES

export function getPetDefinition(petId: PetId): PetDefinition {
  return PET_REGISTRY[petId]
}

export function getPetActions(petId: PetId): readonly PetAction[] {
  return getPetDefinition(petId).actions
}

export function resolvePetAction(
  petId: PetId,
  cue: PetCue = 'idle',
  requestedAction?: PetAction,
): PetAction {
  const definition = getPetDefinition(petId)
  if (requestedAction && definition.actions.includes(requestedAction)) return requestedAction
  const mapped = definition.cueMap[cue]
  if (definition.actions.includes(mapped)) return mapped
  return definition.actions.find(actionName => actionName === 'idle') ?? definition.actions[0]!
}

export function getPetActionDefinition(
  petId: PetId,
  actionName: PetAction,
): PetActionDefinition {
  const definition = getPetDefinition(petId).actionDefinitions[actionName]
  if (!definition) throw new Error(`Unknown pet action: ${petId}/${actionName}`)
  return definition
}

export function getPetFrame(petId: PetId, actionName: PetAction, frameIndex: number): string {
  const definition = getPetActionDefinition(petId, actionName)
  const frameNumber = String((frameIndex % definition.frameCount) + 1).padStart(2, '0')
  const baseUrl = import.meta.env.BASE_URL.endsWith('/')
    ? import.meta.env.BASE_URL
    : `${import.meta.env.BASE_URL}/`
  return `${baseUrl}pets/${petId}/frames/${actionName}/${petId}_${actionName}_${frameNumber}.png`
}

export function getHotbloodPetFrame(actionName: PetAction, frameIndex: number): string {
  return getPetFrame('hotblood', actionName, frameIndex)
}
