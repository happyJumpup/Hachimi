export const toSafeOriginUrl = (value: string | null | undefined): string | undefined => {
  if (!value) return undefined
  try {
    const parsed = new URL(value)
    if (!['http:', 'https:'].includes(parsed.protocol) || parsed.username || parsed.password) {
      return undefined
    }
    return parsed.href
  } catch {
    return undefined
  }
}
