import { expect, test } from '@playwright/test'

test('quick plan completes through save-as, rest recovery, Pet, record, and poster share', async ({ page }) => {
  await page.addInitScript(() => {
    Object.defineProperty(navigator, 'canShare', { configurable: true, value: () => true })
    Object.defineProperty(navigator, 'share', {
      configurable: true,
      value: async () => undefined,
    })
  })

  await page.goto('/mine')
  await page.getByLabel('性别').selectOption('female')
  await page.getByLabel('年龄').fill('28')
  await page.getByLabel('身高').fill('165')
  await page.getByLabel('体重').fill('55')
  await page.getByRole('button', { name: '保存档案' }).click()
  await expect(page.getByText('训练档案已保存到本机')).toBeVisible()

  await page.getByRole('button', { name: '已显示' }).click()
  await page.getByRole('button', { name: '使用快速体验方案' }).click()
  await expect(page).toHaveURL(/\/plan$/)

  const cards = page.locator('.plan-card')
  await expect(cards).toHaveCount(3)
  await cards.nth(0).getByRole('spinbutton').nth(0).fill('1')
  await cards.nth(0).getByRole('spinbutton').nth(1).fill('1')
  await cards.nth(0).getByRole('spinbutton').nth(2).fill('3')
  for (const index of [1, 2]) {
    await cards.nth(index).getByRole('spinbutton').nth(0).fill('1')
    await cards.nth(index).getByRole('spinbutton').nth(1).fill('1')
    await cards.nth(index).getByRole('spinbutton').nth(2).fill('0')
  }

  await page.getByRole('button', { name: '另存为' }).click()
  await page.getByLabel('新方案名称').fill('评委演示方案')
  await page.getByRole('button', { name: '保存副本' }).click()
  await expect(page.getByText('已另存为新方案')).toBeVisible()
  await page.getByRole('button', { name: '开始训练' }).click()

  await expect(page).toHaveURL(/\/training$/)
  await expect(page.getByRole('heading', { name: '肩部绕环' })).toBeVisible()
  await expect(page.getByRole('button', { name: '显示哈肌咪' })).toBeVisible()
  await page.getByRole('button', { name: '显示哈肌咪' }).click()
  await expect(page.locator('.training-pet img')).toBeVisible()
  await page.getByRole('button', { name: '开始本组' }).click()
  await expect(page.getByText('组间休息', { exact: true }).first()).toBeVisible({ timeout: 5_000 })

  await page.getByRole('link', { name: '返回方案' }).click()
  await page.getByRole('link', { name: '我的训练' }).click()
  await expect(page.getByRole('link', { name: /休息中|休息结束/ })).toBeVisible()
  await page.reload()
  await page.waitForTimeout(3_100)
  await page.getByRole('link', { name: /休息结束|准备继续训练/ }).click()

  await expect(page.getByText('准备继续', { exact: true }).first()).toBeVisible()
  await page.getByRole('button', { name: '继续下一组' }).click()
  await expect(page.getByRole('heading', { name: '站姿弯举' })).toBeVisible()
  await page.getByRole('button', { name: '完成本组' }).click()
  await page.getByRole('button', { name: '继续下一组' }).click()
  await expect(page.getByRole('heading', { name: '窄距俯卧撑' })).toBeVisible()
  await page.getByRole('button', { name: '完成本组' }).click()

  await expect(page.getByRole('heading', { name: '训练完成' })).toBeVisible()
  await page.getByRole('link', { name: '查看训练结果' }).click()
  await expect(page).toHaveURL(/\/result\//)
  await expect(page.getByText('约', { exact: false }).first()).toBeVisible()
  await page.getByRole('button', { name: '分享海报' }).click()
  await expect(page.getByText('已打开分享')).toBeVisible()

  await page.getByRole('link', { name: '我的训练' }).click()
  await expect(page.getByText('评委演示方案', { exact: true }).first()).toBeVisible()
  await expect(page.getByText('已完成', { exact: true }).first()).toBeVisible()
})
