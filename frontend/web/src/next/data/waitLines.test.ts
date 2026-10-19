/**
 * 等待页文案的纯逻辑测试。`node --test src/next/data/waitLines.test.ts`。
 *
 * 只测「已等待 Ns」这一组（2026-10-19 找回）的三道闸：15s 起步、
 * writing/structuring 不报、取消/失败不报——这三条都是"实测踩过才立的规矩"，
 * 纯函数必须能被 node --test 直接喂样本，不靠看页面盯出来。
 */
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { benchLine, waitedSuffix } from './waitLines.ts'
import { EMPTY_RUN } from './model.ts'

const run = (over: Partial<typeof EMPTY_RUN>) => ({ ...EMPTY_RUN, ...over })

test('15 秒内不报「已等待」——短轮报时只是噪音', () => {
  assert.equal(waitedSuffix(run({ elapsed: 14_000, currentStage: 'searching' })), '')
  assert.equal(waitedSuffix(run({ elapsed: 15_000, currentStage: 'searching' })), '已等待 15s')
})

test('writing / structuring 阶段不报——那时正文/卡片正在到达，页面本身在动', () => {
  assert.equal(waitedSuffix(run({ elapsed: 30_000, currentStage: 'structuring' })), '')
  assert.equal(waitedSuffix(run({ elapsed: 30_000, currentStage: 'writing' })), '')
})

test('取消 / 失败后不报——那一轮的时间已经停了', () => {
  assert.equal(
    waitedSuffix(run({ elapsed: 30_000, currentStage: 'searching', orphaned: true, orphanCause: 'cancelled' })),
    '',
  )
  assert.equal(waitedSuffix(run({ elapsed: 30_000, currentStage: 'searching', status: 'failed' })), '')
})

test('工位句尾接「· 已等待 Ns」，无后缀时原句逐字不变', () => {
  const withWait = benchLine(run({ elapsed: 23_000, currentStage: 'searching' }))
  assert.match(withWait.text, /· 已等待 23s$/)
  const noWait = benchLine(run({ elapsed: 3_000, currentStage: 'searching' }))
  assert.equal(noWait.text, '正在查证这顿饭的做法')
})
