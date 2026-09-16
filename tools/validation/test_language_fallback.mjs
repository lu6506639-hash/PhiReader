import assert from 'node:assert/strict'
import { displayAuthors } from '../../src/lib/authorDisplay.ts'

assert.equal(displayAuthors('作者信息未提供', 'en'), 'Author information not provided')
assert.equal(displayAuthors('作者信息未提供', 'zh-CN'), '作者信息未提供')
assert.equal(displayAuthors('Ada Lovelace', 'en'), 'Ada Lovelace')

console.log('language fallback regression checks passed')
