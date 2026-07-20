import { expect, test } from '@playwright/test'

test('visitor can install the labelled quick plan from My Training', async ({ page }) => {
  await page.goto('/mine')

  await expect(page.getByRole('heading', { name: '我的训练' })).toBeVisible()
  await expect(page.getByText('快速体验方案', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: '使用快速体验方案' }).click()

  await expect(page).toHaveURL(/\/plan$/)
  await expect(page.getByLabel('方案名称')).toHaveValue('8 分钟手臂唤醒')
  await expect(page.getByText('这个方案不是 AI 分析结果')).toBeVisible()

  const firstAction = page.locator('.plan-card').first()
  const controls = [
    firstAction.getByRole('button', { name: '上移' }),
    firstAction.getByRole('button', { name: '下移' }),
    firstAction.getByRole('button', { name: '按次数' }),
    firstAction.getByRole('button', { name: '按时长' }),
    firstAction.getByRole('button', { name: '复制' }),
    firstAction.getByRole('button', { name: '删除' }),
  ]
  for (const control of controls) {
    const box = await control.boundingBox()
    expect(box, `${await control.getAttribute('aria-label') ?? await control.textContent()} 应可单手点按`).not.toBeNull()
    expect(box!.width).toBeGreaterThanOrEqual(44)
    expect(box!.height).toBeGreaterThanOrEqual(44)
  }
})
