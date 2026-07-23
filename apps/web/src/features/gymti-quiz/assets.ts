export type GymtiType = 'IMNB' | 'KCAL' | 'HIDE' | 'LIFE' | 'CURV' | 'BOOM' | 'WINN'

export type CoachPetStyle =
  | '热血鼓励型'
  | '温柔陪伴型'
  | '毒舌督促型'
  | '专业数据型'
  | '幽默话痨型'
  | '挑战突破型'
  | '佛系陪练型'

const assetBase = import.meta.env.BASE_URL.replace(/\/?$/, '/')

export const gymtiAssetUrls = {
  IMNB: `${assetBase}gymti/types/imnb.webp`,
  KCAL: `${assetBase}gymti/types/kcal.webp`,
  HIDE: `${assetBase}gymti/types/hide.webp`,
  LIFE: `${assetBase}gymti/types/life.webp`,
  CURV: `${assetBase}gymti/types/curv.webp`,
  BOOM: `${assetBase}gymti/types/boom.webp`,
  WINN: `${assetBase}gymti/types/winn.webp`,
} as const satisfies Record<GymtiType, string>

export const coachCatAssetUrls = {
  热血鼓励型: `${assetBase}trainpal/pets/hotblood/idle/01.webp`,
  温柔陪伴型: `${assetBase}trainpal/pets/gentle/idle/01.webp`,
  毒舌督促型: `${assetBase}trainpal/pets/snarky/idle/01.webp`,
  专业数据型: `${assetBase}trainpal/pets/analyst/idle/01.webp`,
  幽默话痨型: `${assetBase}trainpal/pets/comedian/idle/01.webp`,
  挑战突破型: `${assetBase}trainpal/pets/challenger/idle/01.webp`,
  佛系陪练型: `${assetBase}trainpal/pets/zen/idle/01.webp`,
} as const satisfies Record<CoachPetStyle, string>
