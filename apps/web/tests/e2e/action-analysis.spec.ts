import { expect, test } from '@playwright/test'

test('video candidate can be reviewed, added, and restored from the draft', async ({ page }) => {
  await page.goto('/')
  const sourceSelect = page.getByLabel('来源视频')
  await expect(sourceSelect).toHaveValue('legacy-arm-workout')
  await expect(sourceSelect.locator('option:checked')).toContainText('哈基米手臂训练')

  await page.locator('video').evaluate((video: HTMLVideoElement) => {
    video.currentTime = 2
  })
  await page.getByRole('button', { name: /添加动作/ }).click()

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

  await page.reload()
  await expect(
    page.locator('.plan-list').getByRole('textbox', { name: '动作名称' }).first(),
  ).toHaveValue('拖拽弯举')

  await page.getByRole('button', { name: /创建动作/ }).click()
  await page.getByLabel('动作名称').last().fill('平板支撑')
  await page.getByRole('button', { name: '按时长' }).last().click()
  await page.getByRole('button', { name: '加入草稿' }).click()
  await expect(
    page.locator('.plan-list').getByRole('textbox', { name: '动作名称' }).last(),
  ).toHaveValue('平板支撑')
})
