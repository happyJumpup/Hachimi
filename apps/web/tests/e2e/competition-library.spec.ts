import { expect, test } from '@playwright/test'

test('visitor can install the labelled quick plan from My Training', async ({ page }) => {
  await page.goto('/mine')

  await expect(page.getByRole('heading', { name: '我的训练' })).toBeVisible()
  await expect(page.getByText('快速体验方案', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: '使用快速体验方案' }).click()

  await expect(page).toHaveURL(/\/plan$/)
  await expect(page.getByLabel('方案名称')).toHaveValue('8 分钟手臂唤醒')
  await expect(page.getByText('这个方案不是 AI 分析结果')).toBeVisible()
})
