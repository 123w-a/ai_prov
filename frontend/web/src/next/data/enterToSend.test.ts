/**
 * 回车语义的纯逻辑测试。`node --test src/next/data/enterToSend.test.ts`。
 *
 * 对着验收点名的边界写，不是对着实现写：Enter 发送；Shift/Alt/Ctrl/Meta+Enter 换行；
 * 输入法拼字回车**绝不**发送（isComposing 与 keyCode 229 是两个必须各自成立的判据，
 * 不能只留一个）；非回车键不参与判定。
 */
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { enterIntent } from './enterToSend.ts'
import type { EnterKeyLike } from './enterToSend.ts'

const key = (over: Partial<EnterKeyLike> = {}): EnterKeyLike => ({
  key: 'Enter',
  isComposing: false,
  keyCode: 13,
  shiftKey: false,
  altKey: false,
  ctrlKey: false,
  metaKey: false,
  ...over,
})

test('Enter（不带修饰键）就是发送', () => {
  assert.equal(enterIntent(key()), 'send')
})

test('非回车键一律不参与：字母 / Escape / 单按 Shift / IME 的 Process', () => {
  for (const k of ['a', 'Escape', 'Shift', 'Tab', 'Process', '']) {
    assert.equal(enterIntent(key({ key: k })), 'ignore')
  }
})

test('输入法拼字中（isComposing）的回车是选词，不是发送', () => {
  assert.equal(enterIntent(key({ isComposing: true })), 'ignore')
})

test('keyCode 229 单独成立时也拦住（部分 IME 只给得出这一个信号）', () => {
  assert.equal(enterIntent(key({ keyCode: 229 })), 'ignore')
})

test('拼字优先于修饰键：拼字中的 Shift+Enter 仍归输入法', () => {
  assert.equal(enterIntent(key({ isComposing: true, shiftKey: true })), 'ignore')
  assert.equal(enterIntent(key({ keyCode: 229, ctrlKey: true })), 'ignore')
})

test('Shift / Alt / Ctrl / Meta + Enter 全部按换行走，不抢快捷键', () => {
  assert.equal(enterIntent(key({ shiftKey: true })), 'newline')
  assert.equal(enterIntent(key({ altKey: true })), 'newline')
  assert.equal(enterIntent(key({ ctrlKey: true })), 'newline')
  assert.equal(enterIntent(key({ metaKey: true })), 'newline')
})

test('组合修饰键也还是换行', () => {
  assert.equal(enterIntent(key({ ctrlKey: true, shiftKey: true })), 'newline')
  assert.equal(enterIntent(key({ metaKey: true, altKey: true })), 'newline')
})
