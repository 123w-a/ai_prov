/**
 * 历史数据层的纯逻辑测试。`node --test src/next/data/historyTurns.test.ts`。
 *
 * 对着旧 App.tsx 那套真实判据写，不是对着实现写：结构化三判据、纯文本、
 * 坏 JSON、空对象、JSON 数组、pending、cancelled、image_cancelled（有文字/无文字）、
 * 空 answer、用户图优先级、非法时间、recordId 优先去重、内容兜底去重。
 */
import { test } from 'node:test'
import assert from 'node:assert/strict'
import {
  appendHistoryTurnUnique,
  parseHistoryTime,
  runToHistoryTurn,
  sessionMessagesToHistoryTurns,
  updateHistoryTurnAnswer,
} from './historyTurns.ts'
import type { HistoryTurn } from './historyTurns.ts'
import type { ChefAnswer, SessionMessage } from '../../types.ts'
import { EMPTY_RUN } from './model.ts'

const rec = (over: Partial<SessionMessage> = {}): SessionMessage => ({
  id: 1,
  user_text: '今晚吃什么',
  answer: '',
  ...over,
})

const CARD = JSON.stringify({
  opening: '来一份番茄炒蛋。',
  recipes: [{ name: '番茄炒蛋', intro: '', difficulty: 2, nutrition: 3, seasonings: [], steps: ['切'] }],
  chef_tip: '少油。',
})

test('结构化 JSON：JSON 成功 + 根非数组对象 + recipes 数组，三者齐才算卡', () => {
  const [t] = sessionMessagesToHistoryTurns([rec({ answer: CARD })])
  assert.equal(t.answerKind, 'structured')
  assert.equal(t.answer?.opening, '来一份番茄炒蛋。')
  assert.equal(t.answer?.recipes[0].name, '番茄炒蛋')
  assert.equal(t.body, '')
})

test('纯文本回答按 prose 原样保留', () => {
  const [t] = sessionMessagesToHistoryTurns([rec({ answer: '今晚喝粥吧，好消化。' })])
  assert.equal(t.answerKind, 'prose')
  assert.equal(t.answer, null)
  assert.equal(t.body, '今晚喝粥吧，好消化。')
})

test('坏 JSON（以 { 开头但解析失败）按原始纯文本，不丢内容', () => {
  const bad = '{"opening": "断了'
  const [t] = sessionMessagesToHistoryTurns([rec({ answer: bad })])
  assert.equal(t.answerKind, 'prose')
  assert.equal(t.answer, null)
  assert.equal(t.body, bad)
})

test('空对象 {} 不算结构化（recipes 不是数组），按纯文本', () => {
  const [t] = sessionMessagesToHistoryTurns([rec({ answer: '{}' })])
  assert.equal(t.answerKind, 'prose')
  assert.equal(t.answer, null)
  assert.equal(t.body, '{}')
})

test('JSON 数组不算结构化（根是数组），按纯文本', () => {
  const raw = '[{"recipes":[]}]'
  const [t] = sessionMessagesToHistoryTurns([rec({ answer: raw })])
  assert.equal(t.answerKind, 'prose')
  assert.equal(t.answer, null)
  assert.equal(t.body, raw)
})

test('__pending__ 只标「生成中」，不伪造成答案', () => {
  const [t] = sessionMessagesToHistoryTurns([rec({ answer: '__pending__' })])
  assert.equal(t.answerKind, 'pending')
  assert.equal(t.answer, null)
  assert.equal(t.body, '')
})

test('cancelled / __cancelled__ 只留用户问题与图，标 cancelled', () => {
  for (const over of [{ cancelled: true, answer: CARD }, { answer: '__cancelled__' }] as Partial<SessionMessage>[]) {
    const [t] = sessionMessagesToHistoryTurns([rec(over)])
    assert.equal(t.cancelled, true)
    assert.equal(t.answerKind, 'none')
    assert.equal(t.answer, null)
    assert.equal(t.body, '')
    assert.equal(t.request, '今晚吃什么')
  }
})

test('image_cancelled：结构化卡与图丢弃，只留 opening || chef_tip', () => {
  const [t] = sessionMessagesToHistoryTurns([rec({ answer: CARD, image_cancelled: true })])
  assert.equal(t.answer, null)
  assert.equal(t.answerKind, 'prose')
  assert.equal(t.body, '来一份番茄炒蛋。') // opening 优先于 chef_tip
})

test('image_cancelled 只剩原纯文本时按纯文本留', () => {
  const [t] = sessionMessagesToHistoryTurns([rec({ answer: '纯文字补充一句', image_cancelled: true })])
  assert.equal(t.answerKind, 'prose')
  assert.equal(t.body, '纯文字补充一句')
})

test('image_cancelled 且没有任何文字时不补造回答', () => {
  const [t] = sessionMessagesToHistoryTurns([rec({ answer: CARD, image_cancelled: true })].map((r, i) =>
    i === 0 ? { ...r, answer: JSON.stringify({ opening: '', chef_tip: '', recipes: [{ name: 'x', steps: [] }] }) } : r,
  ))
  assert.equal(t.answerKind, 'none')
  assert.equal(t.body, '')
})

test('空 answer 不生成回答块', () => {
  for (const raw of ['', '   ']) {
    const [t] = sessionMessagesToHistoryTurns([rec({ answer: raw })])
    assert.equal(t.answerKind, 'none')
    assert.equal(t.answer, null)
    assert.equal(t.body, '')
  }
})

test('用户图优先级：user_image_url 定义过就用它，undefined 才退回 image_url', () => {
  const [a] = sessionMessagesToHistoryTurns([rec({ user_image_url: '/u.png', image_url: '/i.png' })])
  assert.equal(a.requestImageUrl, '/u.png')
  const [b] = sessionMessagesToHistoryTurns([rec({ image_url: '/i.png' })])
  assert.equal(b.requestImageUrl, '/i.png')
  // 定义过但值是 null：判据是 !== undefined，所以取 null，不再看 image_url
  const [c] = sessionMessagesToHistoryTurns([rec({ user_image_url: null, image_url: '/i.png' })])
  assert.equal(c.requestImageUrl, null)
})

test('缺图缺文的用户轮：请求补「（图片）」以外不编内容', () => {
  const [t] = sessionMessagesToHistoryTurns([rec({ user_text: '', answer: '' })])
  assert.equal(t.request, '（图片）')
  assert.equal(t.answerKind, 'none')
})

test('缺失或非法时间戳统一 null', () => {
  assert.equal(parseHistoryTime(undefined), null)
  assert.equal(parseHistoryTime(''), null)
  assert.equal(parseHistoryTime('不是时间'), null)
  assert.equal(typeof parseHistoryTime('2026-10-19 19:30:00'), 'number')
  const [t] = sessionMessagesToHistoryTurns([rec({ time: '坏时间' })])
  assert.equal(t.askedAt, null)
})

test('去重先比较 recordId：命中即用新轮替换旧轮（图片轮更新 answer 后旧副本缺图）', () => {
  const oldTurn: HistoryTurn = {
    key: 'rec-1',
    recordId: 1,
    request: 'q',
    requestImageUrl: null,
    askedAt: null,
    answer: null,
    body: '旧',
    answerKind: 'prose',
  }
  const fresh: HistoryTurn = {
    key: 'rec-1',
    recordId: 1,
    request: 'q',
    requestImageUrl: null,
    askedAt: null,
    answer: JSON.parse(CARD) as ChefAnswer,
    body: '',
    answerKind: 'structured',
  }
  const out = appendHistoryTurnUnique([oldTurn], fresh)
  assert.equal(out.length, 1)
  assert.equal(out[0].answerKind, 'structured')
  assert.equal(out[0].answer?.opening, '来一份番茄炒蛋。')
})

test('无 recordId 时按规范化内容兜底去重：命中跳过，不命中追加', () => {
  const base: HistoryTurn = {
    key: 'run-a',
    request: '今晚   吃什么',
    requestImageUrl: null,
    askedAt: null,
    answer: null,
    body: '喝粥  吧',
    answerKind: 'prose',
  }
  const same: HistoryTurn = { ...base, key: 'run-b', request: '今晚 吃什么', body: '喝粥 吧' }
  assert.equal(appendHistoryTurnUnique([base], same).length, 1)
  const other: HistoryTurn = { ...base, key: 'run-c', body: '换一个' }
  assert.equal(appendHistoryTurnUnique([base], other).length, 2)
})

test('runToHistoryTurn：成功轮不标 unfinished，失败/中断轮标', () => {
  const ok = runToHistoryTurn({
    ...EMPTY_RUN,
    status: 'succeeded',
    request: 'q',
    body: '正文',
    askedAt: 123,
    origin: { sessionId: 's', recordId: 7 },
  })
  assert.equal(ok.recordId, 7)
  assert.equal(ok.unfinished, undefined)
  assert.equal(ok.answerKind, 'prose')

  const failed = runToHistoryTurn({ ...EMPTY_RUN, status: 'failed', request: 'q', body: '半截', error: 'x' })
  assert.equal(failed.unfinished, true)
  assert.equal(failed.recordId, undefined)
})

test('runToHistoryTurn：图片轮（imageTurn）不该被归档——由调用方挡，这里只管转换', () => {
  // 转换本身不挑轮种；守门在 TonightApp（imageTurn 恒不 append）。
  const t = runToHistoryTurn({ ...EMPTY_RUN, status: 'succeeded', request: '给「x」配张图', body: '' })
  assert.equal(t.answerKind, 'none')
})

test('updateHistoryTurnAnswer：按 recordId 回写新 answer，未命中原样返回', () => {
  const turns: HistoryTurn[] = [
    { key: 'rec-1', recordId: 1, request: 'q', requestImageUrl: null, askedAt: null, answer: null, body: 'x', answerKind: 'prose' },
    { key: 'rec-2', recordId: 2, request: 'q2', requestImageUrl: null, askedAt: null, answer: null, body: 'y', answerKind: 'prose' },
  ]
  const out = updateHistoryTurnAnswer(turns, 1, JSON.parse(CARD) as ChefAnswer)
  assert.equal(out[0].answerKind, 'structured')
  assert.equal(out[0].answer?.recipes[0].name, '番茄炒蛋')
  assert.equal(out[1].body, 'y')
  assert.equal(updateHistoryTurnAnswer(turns, 99, JSON.parse(CARD) as ChefAnswer), turns)
})
