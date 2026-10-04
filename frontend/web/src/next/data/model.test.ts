/**
 * 需求记录时刻的读法测试。用 `node --test` 直接跑（本机 node 支持 type stripping）：
 *
 *     node --test src/next/data/model.test.ts
 *
 * 为什么这几个样本值得写：`formatStamp` 的判据是**本地日历日**，而它要服务的
 * 场景全是边界——凌晨一点看昨晚 23 点的记录、跨月的"昨天"、跨年的"昨天"。
 * 这三种情况在页面上要靠真实时间才碰得到，靠肉眼测就是碰运气。
 *
 * 样本一律用本地时间构造（new Date(y, m-1, d, h, mi)）：如果改用 UTC 字符串，
 * 测试会在东八区通过、在别的时区红——那是测了时区，不是测了这个函数。
 */

import { test } from 'node:test'
import assert from 'node:assert/strict'

import { formatStamp } from './model.ts'

/** 本地时间构造器：月份按人读的 1-12 传。 */
const at = (y: number, mo: number, d: number, h: number, mi: number): number =>
  new Date(y, mo - 1, d, h, mi, 0, 0).getTime()

test('formatStamp：今天 / 昨天 / 更早', () => {
  const now = at(2026, 10, 4, 10, 23)
  assert.equal(formatStamp(at(2026, 10, 4, 10, 23), now), '今天 10:23')
  assert.equal(formatStamp(at(2026, 10, 4, 0, 5), now), '今天 00:05')
  assert.equal(formatStamp(at(2026, 10, 3, 21, 5), now), '昨天 21:05')
  assert.equal(formatStamp(at(2026, 9, 30, 8, 12), now), '09-30 08:12')
})

test('formatStamp：凌晨看昨晚 23 点说「昨天」，不是「今天」', () => {
  // 判据是日历日，不是"24 小时以内"。差 2 小时但隔了一天，必须说昨天。
  const now = at(2026, 10, 4, 1, 0)
  assert.equal(formatStamp(at(2026, 10, 3, 23, 0), now), '昨天 23:00')
})

test('formatStamp：跨月的「昨天」', () => {
  const now = at(2026, 11, 1, 0, 30)
  assert.equal(formatStamp(at(2026, 10, 31, 23, 40), now), '昨天 23:40')
  assert.equal(formatStamp(at(2026, 10, 30, 23, 40), now), '10-30 23:40')
})

test('formatStamp：跨年的「昨天」', () => {
  const now = at(2027, 1, 1, 9, 0)
  assert.equal(formatStamp(at(2026, 12, 31, 22, 10), now), '昨天 22:10')
  assert.equal(formatStamp(at(2026, 12, 30, 22, 10), now), '12-30 22:10')
})

test('formatStamp：分钟与小时补零', () => {
  const now = at(2026, 10, 4, 10, 23)
  assert.equal(formatStamp(at(2026, 10, 4, 8, 5), now), '今天 08:05')
})
