import { expect, test, type Page } from '@playwright/test'

const expectNoHorizontalOverflow = async (page: Page): Promise<void> => {
  const hasOverflow = await page.evaluate(() => (
    document.documentElement.scrollWidth > document.documentElement.clientWidth
  ))
  expect(hasOverflow).toBe(false)
}

const answerThroughProfile = async (page: Page): Promise<void> => {
  await page.goto('/personalize')
  await expect(page.getByRole('heading', { name: /今天本来计划训练/ })).toBeVisible()

  for (let answerIndex = 0; answerIndex < 7; answerIndex += 1) {
    const noMatch = page.locator('button[data-option-id$="_no_match"]')
    await expect(noMatch).toHaveCount(1)
    await noMatch.click()
    await expect(page.getByText('TrainPal 正在挑下一题…')).toBeHidden()
  }

  await expect(page.getByText('最后一题', { exact: true })).toBeVisible()
  await expect(page.getByText(/这题没有“都不像”/)).toBeVisible()
  await expect(page.getByRole('radio', { name: /都不太像/ })).toHaveCount(0)
  await page.locator('button[data-option-id]').first().click()
  await expect(page.getByRole('heading', { name: '完善训练档案' })).toBeVisible()
}

test('GYMTI reaches a guaranteed eighth-question result and persists style only on confirm', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await answerThroughProfile(page)
  await expect(page.getByText('可跳过，不影响测评结果')).toBeVisible()
  await expect(page.locator('.trainpal-coach')).toHaveCount(0)
  await expectNoHorizontalOverflow(page)
  await page.getByRole('button', { name: '跳过' }).click()

  await expect(page.getByText('GYMTI · 测评结果', { exact: true })).toBeVisible()
  await expect(page.locator('.gymti-art img')).toBeVisible()
  await expect(page.locator('.trainpal-coach')).toHaveCount(1)
  await expect(page.locator('[data-primary-action]')).toHaveCount(1)
  await expectNoHorizontalOverflow(page)
  const recommendedFrame = await page.locator('.coach-result .coach-motion__image').getAttribute('src')
  const recommendedStyleId = recommendedFrame?.match(/\/trainpal\/pets\/([^/]+)\//)?.[1]
  expect(recommendedStyleId).toBeTruthy()
  const confirmedStyleId = recommendedStyleId === 'zen' ? 'hotblood' : 'zen'

  await page.getByRole('button', { name: '修改风格' }).click()
  await expect(page.locator('[data-style-id]')).toHaveCount(7)
  await expect(page.locator('.trainpal-coach')).toHaveCount(1)
  await expectNoHorizontalOverflow(page)
  await page.locator(`[data-style-id="${confirmedStyleId}"]`).click()
  await page.locator('.style-action [data-action="confirm"]').click()
  await expect(page).toHaveURL(/\/plan$/)

  await page.goto('/mine')
  await expect(page.locator('.confirmed-coach .trainpal-coach')).toHaveCount(1)
  await expect(page.locator('.confirmed-coach .coach-motion__image')).toHaveAttribute(
    'src',
    new RegExp(`/trainpal/pets/${confirmedStyleId}/`),
  )

  await page.goto('/personalize')
  await expect(page.getByText('GYMTI · 测评结果', { exact: true })).toBeVisible()
  await expect(page.locator('.coach-result .coach-motion__image')).toHaveAttribute(
    'src',
    new RegExp(`/trainpal/pets/${recommendedStyleId}/`),
  )
  await expectNoHorizontalOverflow(page)
})

for (const width of [320, 768] as const) {
  test(`GYMTI profile, result, and style picker remain usable at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: width === 320 ? 844 : 900 })
    await answerThroughProfile(page)
    await expectNoHorizontalOverflow(page)

    await page.getByRole('button', { name: '跳过' }).click()
    await expect(page.getByText('GYMTI · 测评结果', { exact: true })).toBeVisible()
    await expect(page.locator('.gymti-art img')).toBeVisible()
    await expectNoHorizontalOverflow(page)

    await page.getByRole('button', { name: '修改风格' }).click()
    await expect(page.locator('[data-style-id]')).toHaveCount(7)
    await expect(page.locator('.trainpal-coach')).toHaveCount(1)
    await expectNoHorizontalOverflow(page)
  })
}
