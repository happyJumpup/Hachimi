import { expect, test } from '@playwright/test'

test('video candidate can be reviewed, added, and restored from the draft', async ({ page }) => {
  await page.goto('/')
  const sourceSelect = page.getByLabel('来源视频')
  await expect(sourceSelect).toHaveValue('legacy-arm-workout')
  await expect(sourceSelect.locator('option:checked')).toContainText('哈基米手臂训练')

  for (const target of [
    sourceSelect,
    page.getByRole('link', { name: '我的训练' }),
    page.getByRole('link', { name: /方案草稿/ }),
  ]) {
    const box = await target.boundingBox()
    expect(box).not.toBeNull()
    expect(box!.width).toBeGreaterThanOrEqual(44)
    expect(box!.height).toBeGreaterThanOrEqual(44)
  }

  await page.locator('video').evaluate((video: HTMLVideoElement) => {
    video.currentTime = 2
  })
  await page.getByRole('button', { name: /分析视频动作/ }).click()

  await expect(page.getByRole('heading', { name: '找到 1 个动作' })).toBeVisible()
  await expect(
    page.locator('.candidate-panel').getByRole('textbox', { name: '动作名称' }),
  ).toHaveValue('拖拽弯举')
  await page.locator('.candidate-panel').getByRole('button', { name: '按次数' }).click()
  await page.getByRole('button', { name: '加入草稿 · 1' }).click()

  await expect(page).toHaveURL(/\/plan$/)
  await expect(page.getByRole('heading', { name: '训练方案草稿' })).toBeVisible()
  await expect(
    page.locator('.plan-list').getByRole('textbox', { name: '动作名称' }).first(),
  ).toHaveValue('拖拽弯举')
  await expect(page.getByText('规则', { exact: true }).first()).toBeVisible()

  await page.getByRole('link', { name: '继续找动作' }).click()
  await sourceSelect.selectOption('legacy-arm-workout-alt')
  await expect(sourceSelect.locator('option:checked')).toContainText('合成测试来源')
  await page.locator('video').evaluate((video: HTMLVideoElement) => {
    video.currentTime = 2
  })
  await page.getByRole('button', { name: /分析视频动作/ }).click()
  await expect(page.getByRole('heading', { name: '找到 1 个动作' })).toBeVisible()
  const modeButton = page.locator('.candidate-panel').getByRole('button', { name: '按次数' })
  const modeButtonBox = await modeButton.boundingBox()
  expect(modeButtonBox).not.toBeNull()
  expect(modeButtonBox!.width).toBeGreaterThanOrEqual(44)
  expect(modeButtonBox!.height).toBeGreaterThanOrEqual(44)
  await modeButton.click()
  await page.getByRole('button', { name: '加入草稿 · 1' }).click()
  await expect(page.locator('.plan-card')).toHaveCount(2)
  await expect(page.getByText(/合成测试来源/)).toBeVisible()

  await page.reload()
  await expect(page.locator('.plan-card')).toHaveCount(2)

  await page.getByRole('button', { name: /创建动作/ }).click()
  await page.getByLabel('动作名称').last().fill('平板支撑')
  await page.getByRole('button', { name: '按时长' }).last().click()
  await page.getByRole('button', { name: '加入草稿' }).click()
  await expect(
    page.locator('.plan-list').getByRole('textbox', { name: '动作名称' }).last(),
  ).toHaveValue('平板支撑')

  const actionCards = page.locator('.plan-card')
  await actionCards.nth(0).getByRole('spinbutton').nth(0).fill('1')
  await actionCards.nth(0).getByRole('spinbutton').nth(2).fill('0')
  await actionCards.nth(1).getByRole('spinbutton').nth(0).fill('1')
  await actionCards.nth(1).getByRole('spinbutton').nth(2).fill('0')
  await actionCards.nth(2).getByRole('spinbutton').nth(0).fill('1')
  await actionCards.nth(2).getByRole('spinbutton').nth(1).fill('1')
  await actionCards.nth(2).getByRole('spinbutton').nth(2).fill('0')

  await page.getByRole('button', { name: '开始训练' }).click()
  await expect(page).toHaveURL(/\/training$/)
  await expect(page.getByRole('heading', { name: '拖拽弯举' })).toBeVisible()
  await expect(page.locator('.media-stage video')).toBeVisible()

  await page.getByRole('button', { name: '开始本组' }).click()
  await expect(page.getByText('本组进行中', { exact: true }).first()).toBeVisible()
  await page.reload()
  await expect(page.getByText(/已恢复并暂停|训练已暂停/, { exact: true }).first()).toBeVisible()
  await page.getByRole('button', { name: '继续训练' }).click()
  await page.getByRole('button', { name: '完成本组' }).click()

  await expect(page.getByText('准备继续', { exact: true }).first()).toBeVisible()
  await page.getByRole('button', { name: '准备继续' }).click()
  await expect(page.getByRole('heading', { name: '拖拽弯举' })).toBeVisible()
  await expect(page.locator('.media-stage video')).toBeVisible()
  await page.getByRole('button', { name: '完成本组' }).click()

  await expect(page.getByText('准备继续', { exact: true }).first()).toBeVisible()
  await page.getByRole('button', { name: '准备继续' }).click()
  await expect(page.getByRole('heading', { name: '平板支撑' })).toBeVisible()
  await expect(page.getByText('这个动作没有参考视频')).toBeVisible()
  await expect(page.getByRole('heading', { name: '训练完成' })).toBeVisible({ timeout: 5_000 })
  await expect(page.getByText('实际完成量已保存到本机。')).toBeVisible()
})
