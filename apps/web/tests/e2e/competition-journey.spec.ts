import { expect, test } from '@playwright/test'

test('quick plan completes through save-as, rest recovery, TrainPal, record, and poster share', async ({ page }) => {
  await page.addInitScript(() => {
    Object.defineProperty(navigator, 'canShare', { configurable: true, value: () => true })
    Object.defineProperty(navigator, 'share', {
      configurable: true,
      value: async () => undefined,
    })
  })

  await page.goto('/mine')
  await page.getByRole('button', { name: /个人信息 · 可选/ }).click()
  await page.getByLabel('性别').selectOption('female')
  await page.getByLabel('年龄').fill('28')
  await page.getByLabel('身高').fill('165')
  await page.getByLabel('体重').fill('55')
  await page.getByRole('button', { name: '保存档案' }).click()
  await expect(page.getByText('训练档案已保存到本机')).toBeVisible()
  await page.locator('.detail-sheet header').getByRole('button', { name: '关闭详情' }).click()

  await page.getByRole('button', { name: /小猫教练风格/ }).click()
  await page.getByRole('button', { name: '已显示' }).click()
  await page.locator('.detail-sheet header').getByRole('button', { name: '关闭详情' }).click()
  await page.getByRole('navigation', { name: '主要导航' }).getByRole('link', { name: '训练', exact: true }).click()
  await page.getByRole('button', { name: '使用快速体验方案' }).click()
  await expect(page).toHaveURL(/\/plan$/)

  await expect(page.locator('.plan-card')).toHaveCount(3)
  for (const [index, restSeconds] of [[0, '3'], [1, '0'], [2, '0']] as const) {
    await page.locator('.plan-card').nth(index).locator('.action-summary').click()
    const sheet = page.locator('.action-sheet')
    await sheet.getByRole('spinbutton').nth(0).fill('1')
    await sheet.getByRole('spinbutton').nth(1).fill('1')
    await sheet.getByRole('spinbutton').nth(2).fill(restSeconds)
    await sheet.getByRole('button', { name: '完成', exact: true }).click()
  }

  await page.getByRole('button', { name: '另存为' }).click()
  await page.getByLabel('新方案名称').fill('评委演示方案')
  await page.getByRole('button', { name: '保存副本' }).click()
  await expect(page.getByText('已另存为新方案')).toBeVisible()
  await page.getByRole('button', { name: '开始训练' }).click()

  await expect(page).toHaveURL(/\/training$/)
  await expect(page.getByRole('heading', { name: '肩部绕环' })).toBeVisible()
  await expect(page.getByRole('button', { name: '显示 TrainPal' })).toBeVisible()
  await page.getByRole('button', { name: '显示 TrainPal' }).click()
  await expect(page.locator('.training-pet img')).toBeVisible()
  await page.getByRole('button', { name: '开始本组' }).click()
  await expect(page.getByText('组间休息', { exact: true }).first()).toBeVisible({ timeout: 5_000 })

  await page.getByRole('link', { name: '返回方案' }).click()
  await page.getByRole('link', { name: '返回首页' }).click()
  await page.getByRole('navigation', { name: '主要导航' }).getByRole('link', { name: '训练', exact: true }).click()
  await expect(page.getByRole('link', { name: '继续训练', exact: true })).toBeVisible()
  await page.reload()
  await page.waitForTimeout(3_100)
  await page.getByRole('link', { name: '继续训练', exact: true }).click()

  await expect(page.getByText('准备继续', { exact: true }).first()).toBeVisible()
  await page.getByRole('button', { name: '准备好了' }).click()
  await expect(page.getByRole('heading', { name: '站姿弯举' })).toBeVisible()
  await page.getByRole('button', { name: '完成本组' }).click()
  await page.getByRole('button', { name: '准备好了' }).click()
  await expect(page.getByRole('heading', { name: '窄距俯卧撑' })).toBeVisible()
  await page.getByRole('button', { name: '完成本组' }).click()

  await expect(page.getByRole('heading', { name: '训练完成' })).toBeVisible()
  await page.getByRole('link', { name: '查看训练结果' }).click()
  await expect(page).toHaveURL(/\/result\//)
  await expect(page.getByText('约', { exact: false }).first()).toBeVisible()
  await page.getByRole('button', { name: '分享海报' }).click()
  await expect(page.getByText('已打开分享')).toBeVisible()

  await page.getByRole('link', { name: '我的训练' }).click()
  await page.getByRole('button', { name: /训练记录/ }).click()
  await expect(page.getByText('评委演示方案', { exact: true }).first()).toBeVisible()
  await expect(page.getByText('已完成', { exact: true }).first()).toBeVisible()
})

test('TrainPal asset failure does not block early ending and no completion poster is offered', async ({ page }) => {
  await page.route('**/*.webp', (route) => route.abort())
  page.on('dialog', (dialog) => dialog.accept())

  await page.goto('/train')
  await page.getByRole('button', { name: '使用快速体验方案' }).click()
  await page.getByRole('button', { name: '开始训练' }).click()

  await expect(page).toHaveURL(/\/training$/)
  await expect(page.getByText(/训练不受影响/)).toBeVisible()
  await expect(page.getByRole('button', { name: '开始本组' })).toBeEnabled()
  await page.getByText('更多训练操作', { exact: true }).click()
  await page.getByRole('button', { name: '提前结束' }).click()

  await expect(page.getByRole('heading', { name: '已提前结束' })).toBeVisible()
  await page.getByRole('link', { name: '查看训练结果' }).click()
  await expect(page.getByText('提前结束只保留实际记录，不生成完成海报。')).toBeVisible()
  await expect(page.getByRole('button', { name: '分享海报' })).toHaveCount(0)
  await expect(page.getByRole('button', { name: '下载海报' })).toHaveCount(0)

  await page.getByRole('button', { name: '再练一次' }).click()
  await expect(page).toHaveURL(/\/plan$/)
  await expect(page.locator('.plan-card')).toHaveCount(3)
})
