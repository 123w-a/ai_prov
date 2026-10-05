import type { MemberInput } from '../../api/client.ts'

/**
 * 成员编辑器的草稿转换（2026-10-05）。
 *
 * 抽出来有两个原因：一是 blocks 层单文件上限 150 行，转换逻辑占了大半；
 * 二是"档案 ↔ 可编辑文本"本来就是这个项目里的数据层职责，不是画界面的职责。
 *
 * ⚠️ 这里必须覆盖 profile 的**全部**字段。后端 PUT /profile/members/{id} 是
 * **整成员覆盖**（member["profile"] = payload.profile），漏掉谁就等于把谁清空——
 * 而 diet_style 这类字段页面上根本不显示，漏了它不会有任何视觉异常，
 * 只是用户的设置被静默抹掉。
 */

/** 表单内部一律用字符串存（输入框天然如此），提交时才转成后端的形状。 */
export interface MemberDraft {
  name: string
  goal: string
  conditions: string
  allergens: string
  restricts: string
  dislikes: string
  taste_notes: string
  diet_style: string
  height_cm: string
  weight_kg: string
  age: string
  sex: string
}

/** 字段表：key 与 MemberDraft 同构，顺序即屏幕顺序。narrow 的排在数字列。 */
export const MEMBER_FIELDS: Array<{
  k: keyof MemberDraft
  label: string
  ph?: string
  narrow?: boolean
}> = [
  { k: 'name', label: '姓名' },
  { k: 'goal', label: '目标', ph: '如 增肌、孕期营养均衡' },
  { k: 'conditions', label: '身体状况', ph: '如 孕妇、高血压（多项用顿号隔开）' },
  { k: 'allergens', label: '过敏原', ph: '多项用顿号隔开' },
  { k: 'restricts', label: '医嘱限制', ph: '如 忌生冷生食、忌酒' },
  { k: 'dislikes', label: '不喜欢的食物', ph: '多项用顿号隔开' },
  { k: 'taste_notes', label: '口味备注', ph: '如 清淡为主、喜欢汤水' },
  { k: 'diet_style', label: '饮食风格', ph: '如 中式家常、低油' },
  { k: 'height_cm', label: '身高 cm', narrow: true },
  { k: 'weight_kg', label: '体重 kg', narrow: true },
  { k: 'age', label: '年龄', narrow: true },
  { k: 'sex', label: '性别', narrow: true },
]

const joinList = (v?: string[]): string => (v ?? []).join('、')

export const toDraft = (initial: MemberInput | null): MemberDraft => {
  const p = initial?.profile
  return {
    name: initial?.name ?? '',
    goal: p?.goal ?? '',
    conditions: joinList(p?.conditions),
    allergens: joinList(p?.allergens),
    restricts: joinList(p?.restricts),
    dislikes: joinList(p?.dislikes),
    taste_notes: joinList(p?.taste_notes),
    diet_style: p?.diet_style ?? '',
    height_cm: p?.basic.height_cm == null ? '' : String(p.basic.height_cm),
    weight_kg: p?.basic.weight_kg == null ? '' : String(p.basic.weight_kg),
    age: p?.basic.age == null ? '' : String(p.basic.age),
    sex: p?.basic.sex ?? '',
  }
}

/** 文本 → 数组。顿号/逗号/分号/空白都是分隔符，空项丢掉。 */
export const splitList = (v: string): string[] =>
  v
    .split(/[、,，;；\s]+/)
    .map((s) => s.trim())
    .filter((s) => s !== '')

/** 数字输入 → number | null。空串是"没填"，不是 0——0kg 是错的，空是对的。 */
export const toNum = (v: string): number | null => {
  const t = v.trim()
  if (t === '') return null
  const n = Number(t)
  return Number.isFinite(n) ? n : null
}

export const toMemberInput = (d: MemberDraft): MemberInput => ({
  name: d.name.trim(),
  profile: {
    conditions: splitList(d.conditions),
    allergens: splitList(d.allergens),
    restricts: splitList(d.restricts),
    goal: d.goal.trim(),
    diet_style: d.diet_style.trim(),
    dislikes: splitList(d.dislikes),
    taste_notes: splitList(d.taste_notes),
    basic: {
      height_cm: toNum(d.height_cm),
      weight_kg: toNum(d.weight_kg),
      age: toNum(d.age),
      sex: (d.sex === 'male' || d.sex === 'female' || d.sex === 'other'
        ? d.sex
        : '') as MemberInput['profile']['basic']['sex'],
    },
  },
})
