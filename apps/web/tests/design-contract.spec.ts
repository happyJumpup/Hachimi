import { readdirSync, readFileSync } from 'node:fs'
import { relative, resolve } from 'node:path'

import { describe, expect, it } from 'vitest'

const sourceRoot = resolve(process.cwd(), 'src')

interface StyleSource {
  file: string
  css: string
}

const collectStyleSources = (directory: string): StyleSource[] =>
  readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const fullPath = resolve(directory, entry.name)
    if (entry.isDirectory()) return collectStyleSources(fullPath)
    if (!entry.name.endsWith('.vue') && !entry.name.endsWith('.css')) return []

    const source = readFileSync(fullPath, 'utf8')
    if (entry.name.endsWith('.css')) {
      return [{ file: relative(sourceRoot, fullPath), css: source }]
    }

    return [...source.matchAll(/<style(?:\s[^>]*)?>([\s\S]*?)<\/style>/g)].map((match) => ({
      file: relative(sourceRoot, fullPath),
      css: match[1] ?? '',
    }))
  })

const styleSources = collectStyleSources(sourceRoot)

describe('web experience design contract', () => {
  it('keeps explicit auxiliary type at 11px or larger', () => {
    const violations = styleSources.flatMap(({ file, css }) => {
      const declarations = [
        ...css.matchAll(/font-size\s*:\s*(\d+(?:\.\d+)?)px/g),
        ...css.matchAll(/font\s*:[^;{}]*?\s(\d+(?:\.\d+)?)px(?=[/\s;])/g),
      ]

      return declarations
        .map((match) => Number(match[1]))
        .filter((size) => size < 11)
        .map((size) => `${file}: ${size}px`)
    })

    expect(violations).toEqual([])
  })

  it('keeps explicitly sized interactive targets at least 44px', () => {
    const violations = styleSources.flatMap(({ file, css }) =>
      [...css.matchAll(/([^{}]+)\{([^{}]*)\}/g)].flatMap((match) => {
        const selector = match[1] ?? ''
        const declarations = match[2] ?? ''
        if (!/(?:^|[\s>+~,])(button|a|input|select)(?=[:.[#\s>+~,]|$)/.test(selector)) return []

        return [...declarations.matchAll(/min-(?:width|height)\s*:\s*(\d+(?:\.\d+)?)px/g)]
          .map((dimension) => Number(dimension[1]))
          .filter((dimension) => dimension < 44)
          .map((dimension) => `${file}: ${selector.trim()} uses ${dimension}px`)
      }),
    )

    expect(violations).toEqual([])
  })

  it('keeps the keyboard focus ring at 2px cyan with a 3px offset', () => {
    const baseStyles = readFileSync(resolve(sourceRoot, 'styles/base.css'), 'utf8')
    const focusRule = baseStyles.match(
      /button:focus-visible,\s*a:focus-visible,\s*select:focus-visible,\s*input:focus-visible\s*\{([^}]*)\}/,
    )

    expect(focusRule).not.toBeNull()
    expect(focusRule?.[1]).toMatch(/outline\s*:\s*2px\s+solid\s+var\(--cyan\)/)
    expect(focusRule?.[1]).toMatch(/outline-offset\s*:\s*3px/)
  })

  it('keeps the initial video stage at a strict 9:16 ratio', () => {
    const videoView = readFileSync(resolve(sourceRoot, 'views/VideoAnalysisView.vue'), 'utf8')
    const stageRule = videoView.match(/\.video-stage\s*\{([^}]*)\}/)

    expect(stageRule).not.toBeNull()
    expect(stageRule?.[1]).toMatch(/aspect-ratio\s*:\s*9\s*\/\s*16/)
    expect(stageRule?.[1]).not.toMatch(/(?:min-)?height\s*:/)
  })
})
