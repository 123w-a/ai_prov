/**
 * 新增字段推导的测试。用 `node --test` 直接跑（本机 node 支持 type stripping）：
 *
 *     node --test src/next/data/fieldFacts.test.ts
 *
 * 样本取自 **2026-10-11 真实后端响应**（.tmp-logs/fields-evidence.txt 里的原样 JSON：
 * GET /profile、PUT /profile/members/me、GET /favorites 的实测报文），不是编的。
 * 唯一例外是「进度有真值」那一条：口径未定，后端此刻给不出这种报文，
 * 它标了 CONTRACT 前缀——测的是字段将来有值时的显示契约，不是观测记录。
 */

import { test } from 'node:test'
import assert from 'node:assert/strict'

import { bmiFact, dislikesIntersection, favoritedAtLabel, memberBodyExtras, progressLine } from './fieldFacts.ts'
import type { FamilyMember } from '../../types.ts'

/** 实测（GET /profile，旧档案）：basic 里两个新键的值为 null。 */
const OLD_MEMBER = {
  id: 'me',
  name: '小美',
  profile: {
    basic: { height_cm: 165.0, weight_kg: null, age: 30, sex: 'female', gestational_weeks: null, body_fat_rate: null },
    conditions: ['孕期'],
    allergens: [],
    dislikes: ['芹菜'],
    goal: '',
    diet_style: '',
    nutrition_progress: { value: null, numerator: null, denominator: null, period: { start: '2026-10-05', end: '2026-10-12' } },
  },
} as unknown as FamilyMember

/** 实测（PUT /profile/members/me 写入后 GET /profile）：18 周 / 24.5%。 */
const FILLED_MEMBER = {
  id: 'me',
  name: '小美',
  profile: {
    basic: { height_cm: 165.0, weight_kg: 58.0, age: 30, sex: 'female', gestational_weeks: 18, body_fat_rate: 24.5 },
    conditions: ['孕期'],
    allergens: [],
    dislikes: [],
    goal: '',
    diet_style: '',
    nutrition_progress: { value: null, numerator: null, denominator: null, period: { start: '2026-10-05', end: '2026-10-12' } },
  },
} as unknown as FamilyMember

test('memberBodyExtras：旧档案的 null 原样是 null，不许变成 0', () => {
  const e = memberBodyExtras(OLD_MEMBER)
  assert.equal(e.gestationalWeeks, null)
  assert.equal(e.bodyFatRate, null)
  assert.notEqual(e.gestationalWeeks, 0)
  assert.notEqual(e.bodyFatRate, 0)
})

test('memberBodyExtras：有真值就原样给出（实测 18 周 / 24.5%）', () => {
  const e = memberBodyExtras(FILLED_MEMBER)
  assert.equal(e.gestationalWeeks, 18)
  assert.equal(e.bodyFatRate, 24.5)
})

test('memberBodyExtras：字段整个不在（undefined）也落 null', () => {
  const bare = { id: 'x', name: 'x', profile: { basic: {}, conditions: [], allergens: [], dislikes: [], goal: '', diet_style: '' } } as unknown as FamilyMember
  const e = memberBodyExtras(bare)
  assert.equal(e.gestationalWeeks, null)
  assert.equal(e.bodyFatRate, null)
  assert.equal(e.nutrition, null)
})

test('memberBodyExtras：体脂率 0 / NaN 落 null（0% 是画不出来的假数），孕周 0 合法', () => {
  const m = (fat: unknown, weeks: unknown) =>
    ({ id: 'x', name: 'x', profile: { basic: { body_fat_rate: fat, gestational_weeks: weeks }, conditions: [], allergens: [], dislikes: [], goal: '', diet_style: '' } }) as unknown as FamilyMember
  assert.equal(memberBodyExtras(m(0, 0)).bodyFatRate, null, '体脂率 0 只可能是坏数据（后端 ge=1）')
  assert.equal(memberBodyExtras(m(NaN, 0)).bodyFatRate, null)
  assert.equal(memberBodyExtras(m('24.5', 0)).bodyFatRate, null, '字符串不认：不替后端做类型转换')
  assert.equal(memberBodyExtras(m(24.5, 0)).gestationalWeeks, 0, '孕周 0 是合法值（刚怀上）')
})

test('progressLine：无真值（实测分母 0）时不许出现任何百分比，并写清为什么没有', () => {
  // 实测（GET /profile，成员「小美」本周没有归属到她的餐次）：
  //   {"value": null, "numerator": 0, "denominator": 0, "period": {...}}
  const line = progressLine({ value: null, numerator: 0, denominator: 0, period: { start: '2026-10-05', end: '2026-10-12' } })
  assert.equal(line, '本周还没有记录到该成员的餐次（未归属或本周未记录），进度待补（口径：该餐营养灯全绿）。')
  assert.ok(!line.includes('%'), '没有真值却出现百分比，就是把占位符当成了真数字')
})

test('progressLine：有真值给可核对表达（实测回填后「我」= 0/3，三条餐都有黄灯）', () => {
  // 实测（真实 meals.json 回填后 GET /profile）：
  //   {"value": 0.0, "numerator": 0, "denominator": 3, ...}
  const line = progressLine({ value: 0, numerator: 0, denominator: 3, period: { start: '2026-10-05', end: '2026-10-12' } })
  assert.equal(line, '本周 0/3 餐达标 · 0%（口径：该餐营养灯全绿）。')
})

test('CONTRACT progressLine：3/5 这种可核对表达（3 条达标 / 5 条已记录）', () => {
  const line = progressLine({ value: 0.6, numerator: 3, denominator: 5, period: { start: '2026-10-05', end: '2026-10-12' } })
  assert.equal(line, '本周 3/5 餐达标 · 60%（口径：该餐营养灯全绿）。')
})

test('CONTRACT progressLine：value 有值但分子分母缺失时只报百分比，不编分母', () => {
  const line = progressLine({ value: 1, numerator: null, denominator: null, period: null })
  assert.equal(line, '本周营养达标 100%（口径：该餐营养灯全绿）。')
})

test('favoritedAtLabel：实测收藏时间 → MM-DD', () => {
  assert.equal(favoritedAtLabel('2026-10-12 12:34:32'), '10-12')
})

test('favoritedAtLabel：旧收藏（实测 favorited_at = null）→ 待补，且不许拿这一轮时间顶', () => {
  assert.equal(favoritedAtLabel(null), '待补')
  assert.equal(favoritedAtLabel(undefined), '待补')
  assert.equal(favoritedAtLabel(''), '待补')
  assert.equal(favoritedAtLabel('2026-08'), '待补', '半截时间不当成日期用')
  assert.notEqual(favoritedAtLabel(null), '08-26', '不许拿 turned_at 2026-08-26 顶上')
})

/* BMI：值取自 2026-10-12 真实页面（#room=profile 实测「我」63kg / 176cm → 20.3，
 * 「小美」165cm / 体重 null → 算不出）。 */

test('bmiFact：真实「我」63kg / 176cm → 20.3，且**不**带任何"缺什么"', () => {
  const f = bmiFact({ height_cm: 176, weight_kg: 63 })
  assert.equal(f.value, '20.3')
  assert.equal(f.missing, null, '算得出就不该有缺项——真值旁边挂着占位签正是要修的那个缺陷')
})

test('bmiFact：真实「小美」165cm / 体重 null → 算不出，并说清缺的是体重', () => {
  const f = bmiFact({ height_cm: 165, weight_kg: null })
  assert.equal(f.value, null)
  assert.equal(f.missing, '体重')
})

test('bmiFact：缺身高、或两项都缺，缺项把话说全', () => {
  assert.equal(bmiFact({ height_cm: null, weight_kg: 63 }).missing, '身高')
  assert.equal(bmiFact({}).missing, '身高与体重')
  assert.equal(bmiFact(null).missing, '身高与体重')
})

test('bmiFact：0 / 负数 / 字符串不算数（0 会算出 Infinity，字符串不替后端转类型）', () => {
  assert.equal(bmiFact({ height_cm: 0, weight_kg: 63 }).value, null)
  assert.equal(bmiFact({ height_cm: 176, weight_kg: 0 }).value, null)
  assert.equal(bmiFact({ height_cm: '176', weight_kg: 63 }).value, null)
  assert.equal(bmiFact({ height_cm: Number.NaN, weight_kg: 63 }).value, null)
  assert.equal(bmiFact({ height_cm: -176, weight_kg: 63 }).value, null)
})

/* 家户级共同忌口 = **交集**。
 *
 * 2026-10-14：同一个缺陷 2026-10-05 只在 household.ts 修过（并集 → 交集），
 * 首屏 firstScreen.ts 那一份 flatMap 并集实现一直留到今天才被发现。
 * 之所以两年都没露出来：真实档案两人恰好都忌芹菜，两种口径结果相同。
 * 这几条把口径钉死，谁再改成并集这里就会红。
 *
 * 成员样本用真实档案值（两人都是 ['芹菜']），混入口味差异是构造的——
 * 后端此刻给不出"只有一人忌香菜"的真实报文。 */

const M = (dislikes: string[]) =>
  ({ id: 'x', name: 'x', profile: { dislikes } }) as unknown as FamilyMember

test('dislikesIntersection：真实档案两人都忌芹菜 → 芹菜', () => {
  assert.deepEqual(dislikesIntersection([M(['芹菜']), M(['芹菜'])]), ['芹菜'])
})

test('dislikesIntersection：只有一个人忌香菜时**不含**香菜（这正是并集会算错的那条）', () => {
  assert.deepEqual(dislikesIntersection([M(['芹菜', '香菜']), M(['芹菜'])]), ['芹菜'])
  assert.deepEqual(dislikesIntersection([M(['香菜']), M(['芹菜'])]), [], '没有共同项就是空，不许混进任何一个人的')
})

test('dislikesIntersection：单人家庭不产生共同项（一个人的忌口是他自己的）', () => {
  assert.deepEqual(dislikesIntersection([M(['芹菜'])]), [])
  assert.deepEqual(dislikesIntersection([]), [])
})

test('dislikesIntersection：任一成员没填忌口 → 空（他与谁都没有"共同"忌口）', () => {
  assert.deepEqual(dislikesIntersection([M(['芹菜']), M([])]), [])
})

test('dislikesIntersection：顺序跟随第一位成员，不排序（排序会让同一个值换位置）', () => {
  assert.deepEqual(dislikesIntersection([M(['香菜', '芹菜']), M(['芹菜', '香菜'])]), ['香菜', '芹菜'])
})

test('dislikesIntersection：同一成员内重复项去重', () => {
  assert.deepEqual(dislikesIntersection([M(['芹菜', '芹菜']), M(['芹菜'])]), ['芹菜'])
})
