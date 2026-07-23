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
  IMNB: `${assetBase}assets/gymti/imnb.png`,
  KCAL: `${assetBase}assets/gymti/kcal.png`,
  HIDE: `${assetBase}assets/gymti/hide.png`,
  LIFE: `${assetBase}assets/gymti/life.png`,
  CURV: `${assetBase}assets/gymti/curv.png`,
  BOOM: `${assetBase}assets/gymti/boom.png`,
  WINN: `${assetBase}assets/gymti/winn.png`,
} as const satisfies Record<GymtiType, string>

export const coachCatAssetUrls = {
  热血鼓励型: `${assetBase}assets/coach-cats/hot-blooded-encourager.png`,
  温柔陪伴型: `${assetBase}assets/coach-cats/gentle-companion.png`,
  毒舌督促型: `${assetBase}assets/coach-cats/sharp-tongue-coach.png`,
  专业数据型: `${assetBase}assets/coach-cats/data-coach.png`,
  幽默话痨型: `${assetBase}assets/coach-cats/chatty-humor-coach.png`,
  挑战突破型: `${assetBase}assets/coach-cats/challenge-breakthrough.png`,
  佛系陪练型: `${assetBase}assets/coach-cats/chill-training-buddy.png`,
} as const satisfies Record<CoachPetStyle, string>
