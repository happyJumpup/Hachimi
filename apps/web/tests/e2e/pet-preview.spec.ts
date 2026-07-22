import { expect, test } from '@playwright/test'

const petCases = [
  { id: 'hotblood', name: '热血鼓励型 hotblood', actions: ['待机 idle', '加油 cheer', '庆祝 hop', '拖动 drag'] },
  { id: 'gentle', name: '温柔陪伴型 gentle', actions: ['待机 idle', '陪伴 comfort', '引导 guide', '休息 rest'] },
  { id: 'snarky', name: '毒舌督促型 snarky', actions: ['待机 idle', '吐槽 mock', '督促 point', '不耐烦 annoyed'] },
  { id: 'analyst', name: '专业数据型 analyst', actions: ['待机 idle', '扫描 scan', '分析 analyze', '播报 report'] },
  { id: 'comedian', name: '幽默话痨型 comedian', actions: ['待机 idle', '话痨 talk', '大笑 laugh', '欢庆 celebrate'] },
  { id: 'challenger', name: '挑战突破型 challenger', actions: ['待机 idle', '挑战 challenge', '倒计时 countdown', '爆发 power-up'] },
  { id: 'zen', name: '佛系陪练型 zen', actions: ['待机 idle', '呼吸 breathe', '点头 nod', '放松 relax'] },
] as const

test('all seven pets load every action and preserve drag position', async ({ page }) => {
  await page.goto('/pet-preview')

  const pet = page.locator('.desktop-pet')
  await expect(page.getByRole('heading', { name: '七猫桌宠动画验收台' })).toBeVisible()

  for (const petCase of petCases) {
    await page.getByRole('button', { name: petCase.name, exact: true }).click()
    await expect(pet).toHaveAttribute('data-pet-id', petCase.id)

    for (const actionName of petCase.actions) {
      await page.getByRole('button', { name: actionName, exact: true }).click()
      const action = actionName.split(' ')[1]
      await expect(pet).toHaveAttribute('data-action', action)
      const image = pet.locator('img')
      await expect(image).toHaveAttribute('src', new RegExp(`/pets/${petCase.id}/frames/${action}/`))
      await expect.poll(
        () => image.evaluate(element => (element as HTMLImageElement).naturalWidth),
      ).toBeGreaterThan(0)
    }
  }

  await page.getByRole('button', { name: '热血鼓励型 hotblood', exact: true }).click()
  await page.getByRole('button', { name: '加油 cheer', exact: true }).click()
  await expect(pet).toHaveAttribute('data-action', 'cheer')

  const before = await pet.boundingBox()
  if (!before) throw new Error('Pet is not visible')
  await page.mouse.move(before.x + before.width / 2, before.y + before.height / 2)
  await page.mouse.down()
  await page.mouse.move(38, 190, { steps: 8 })
  await expect(pet).toHaveAttribute('data-action', 'drag')
  await page.mouse.up()
  await expect(pet).toHaveAttribute('data-action', 'cheer')

  const moved = await pet.boundingBox()
  expect(moved?.x).toBeLessThan(before.x)
  expect(moved?.y).toBeLessThan(before.y)

  await page.reload()
  await expect(page.locator('.desktop-pet')).toHaveAttribute('data-pet-id', 'hotblood')
  const restored = await page.locator('.desktop-pet').boundingBox()
  expect(Math.abs((restored?.x ?? 0) - (moved?.x ?? 0))).toBeLessThan(2)
  expect(Math.abs((restored?.y ?? 0) - (moved?.y ?? 0))).toBeLessThan(2)

  await page.getByRole('button', { name: '重置到右下角' }).click()
  const reset = await page.locator('.desktop-pet').boundingBox()
  expect(Math.abs((reset?.x ?? 0) - before.x)).toBeLessThan(2)
  expect(Math.abs((reset?.y ?? 0) - before.y)).toBeLessThan(2)
})
