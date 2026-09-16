import type { Language } from './i18n'

/**
 * Returns the author label shown in the UI for a paper.
 *
 * The implementation is intentionally kept at this small seam so that
 * language-sensitive fallback labels are tested independently of React.
 */
export function displayAuthors(authors: string, language: Language): string {
  if (authors !== '作者信息未提供') return authors
  return language === 'en' ? 'Author information not provided' : '作者信息未提供'
}
