import { expect, test } from '@playwright/test'
import { fileURLToPath } from 'node:url'

const localVideoPath = fileURLToPath(
  new URL('../../../../tmp/e2e-source.mp4', import.meta.url),
)

test('local video survives analysis, plan preview, and training recovery', async ({ page }) => {
  await page.goto('/')

  await page.locator('.local-file-button input[type="file"]').setInputFiles(localVideoPath)
  await expect(page.getByText('本地来源', { exact: true })).toBeVisible()
  await expect(page.locator('.source-video')).toHaveAttribute('src', /^blob:/)

  await page.getByRole('button', { name: /分析视频动作/ }).click()
  await expect(page.getByRole('heading', { name: '找到 1 个动作' })).toBeVisible()
  const candidatePanel = page.locator('.candidate-panel')
  await candidatePanel.getByRole('button', { name: '跟练执行' }).click()
  await candidatePanel.getByRole('button', { name: '按次数' }).click()
  await page.getByRole('button', { name: '加入草稿 · 1' }).click()

  await expect(page).toHaveURL(/\/plan$/)
  await expect(page.getByText(/本地视频动作 · e2e-source\.mp4/)).toBeVisible()
  await page.getByRole('button', { name: '预览来源视频' }).click()
  await expect(page.locator('.local-preview video')).toHaveAttribute('src', /^blob:/)

  await page.reload()
  await expect(page.getByText(/本地视频动作 · e2e-source\.mp4/)).toBeVisible()
  await page.getByRole('button', { name: '预览来源视频' }).click()
  await expect(page.locator('.local-preview video')).toHaveAttribute('src', /^blob:/)

  const actionCard = page.locator('.plan-card').first()
  await actionCard.getByRole('spinbutton').nth(0).fill('1')
  await actionCard.getByRole('spinbutton').nth(2).fill('0')
  await page.getByRole('button', { name: '开始训练' }).click()

  await expect(page).toHaveURL(/\/training$/)
  await expect(page.getByRole('heading', { name: '拖拽弯举' })).toBeVisible()
  await expect(page.locator('.media-stage video')).toHaveAttribute('src', /^blob:/)
  await page.getByRole('button', { name: '开始本组' }).click()
  await expect(page.getByText('本组进行中', { exact: true }).first()).toBeVisible()

  await page.reload()
  await expect(page.getByText(/已恢复并暂停|训练已暂停/, { exact: true }).first()).toBeVisible()
  await expect(page.locator('.media-stage video')).toHaveAttribute('src', /^blob:/)
  await page.getByRole('button', { name: '继续训练' }).click()
  await page.getByRole('button', { name: '完成本组' }).click()
  await expect(page.getByRole('heading', { name: '训练完成' })).toBeVisible()
})
