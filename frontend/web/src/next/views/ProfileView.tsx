import { useState } from 'react'
import type { MemberInput } from '../../api/client.ts'
import { MemberForm } from '../blocks/MemberForm.tsx'
import type { FamilyData } from '../data/household.ts'

/**
 * 「身体档案」房间（2026-10-05）。
 *
 * 这一间回答的是用户原话的那个问题：「我建立的家庭画像不知道从哪个入口进入，
 * 也不知道如何增加新家庭人员和减家庭成员」。此前成员只出现在 Shell 的抽屉里，
 * 抽屉又只能**切换**，看不到一个人的全貌，更没有任何增删改——而旧前端
 * components/FamilyPanel.tsx 其实早就做完了这套。
 *
 * 两条纪律：
 *   1 props-only，这里**不做任何 I/O**。档案由 app 用 useHousehold 读一次再传进来，
 *     与起手页、等待页读的是同一份——各自去拉会让三页在档案刚改过的一瞬间显示不同的人
 *     （AskView 的文件头已经为此写过一次理由，这里是同一个理由）。
 *     增删改也一样：动作由上层传入，本组件只负责"什么时候请求这一次动作"。
 *   2 空值不画那一行。后端给不出的事实就不占位，绝不写「无」来把空白撑满——
 *     「无」和「不知道」在健康信息上不是一回事。
 *
 * 后端的边界（本组件据此禁用按钮，而不是等它报错）：
 *   · 最多 8 人（POST 超限返回 400）
 *   · 至少保留 1 人（DELETE 到最后一人返回 400）
 *   · PUT 是**整成员覆盖**，所以 MemberForm 必须覆盖 profile 的全部字段。
 */

/** 事实行的取值：把数组与单值统一成一句可读的话；空则返回 null（调用方不画这一行）。 */
function line(value: string | string[] | null | undefined): string | null {
  if (Array.isArray(value)) {
    const kept = value.filter((v) => v && v.trim() !== '')
    return kept.length > 0 ? kept.join('、') : null
  }
  const one = (value ?? '').trim()
  return one === '' ? null : one
}

/** 后端 sex 是 male/female 这种枚举值，直接放上页面就是英文漏进界面
 *  （实测：卡片上出现过「30 岁 · female」）。这里只翻译**已知的三个**，
 *  认不出的值原样保留——不猜、不吞，否则档案里真写了别的东西会静默消失。 */
const SEX_LABEL: Record<string, string> = { male: '男', female: '女', other: '其他' }

/** 身高体重只在两者都有时才写，避免出现「176cm · —」这种半截话（同 familyFacts）。 */
function bodyLine(basic: FamilyData['members'][number]['profile']['basic']): string | null {
  if (!basic) return null
  const bits: string[] = []
  if (basic.height_cm) bits.push(`${basic.height_cm}cm`)
  if (basic.weight_kg) bits.push(`${basic.weight_kg}kg`)
  if (basic.age) bits.push(`${basic.age} 岁`)
  if (basic.sex) bits.push(SEX_LABEL[basic.sex] ?? basic.sex)
  return bits.length > 0 ? bits.join(' · ') : null
}

/** 一个成员 → 可提交的 MemberInput。编辑时要原样带上所有字段，不能只带页面上显示的那些。 */
function toInput(m: FamilyData['members'][number]): MemberInput {
  const p = m.profile
  return {
    name: m.name,
    profile: {
      conditions: [...(p.conditions ?? [])],
      allergens: [...(p.allergens ?? [])],
      restricts: [...(p.restricts ?? [])],
      goal: p.goal ?? '',
      diet_style: p.diet_style ?? '',
      dislikes: [...(p.dislikes ?? [])],
      taste_notes: [...(p.taste_notes ?? [])],
      basic: {
        height_cm: p.basic?.height_cm ?? null,
        weight_kg: p.basic?.weight_kg ?? null,
        age: p.basic?.age ?? null,
        sex: (p.basic?.sex ?? '') as MemberInput['profile']['basic']['sex'],
      },
    },
  }
}

export function ProfileView({
  family,
  shared,
  onPick,
  onAdd,
  onUpdate,
  onDelete,
  switching,
  switchErr,
  busy,
  writeErr,
}: {
  family: FamilyData | null
  /** 全家都忌的那几项（来自 familyFacts 的交集推导，不在这里重算一遍）。 */
  shared: string | null
  onPick: (id: string) => void
  onAdd: (input: MemberInput) => void
  onUpdate: (id: string, input: MemberInput) => void
  onDelete: (id: string) => void
  /** 正在切换的成员 id，非 null 时禁用所有切换按钮，避免连点发出一串写请求。 */
  switching: string | null
  switchErr: string | null
  /** 增/改/删请求进行中。 */
  busy: boolean
  writeErr: string | null
}) {
  /** 谁在编辑：成员 id；'new' 表示正在新增；null 表示没人在编辑。 */
  const [editing, setEditing] = useState<string | null>(null)
  /** 谁在等删除确认。删除不可逆，所以要点两下——但不弹原生 confirm（它阻塞且样式不可控）。 */
  const [confirming, setConfirming] = useState<string | null>(null)

  if (!family || family.members.length === 0) {
    return (
      <section className="pf">
        <h2 className="pf-h">身体档案</h2>
        <p className="pf-empty">档案还没有读出来，先不猜家里有谁。</p>
      </section>
    )
  }

  const active = family.members.find((m) => m.id === family.active_id) ?? null
  const onlyOne = family.members.length <= 1
  const full = family.members.length >= 8

  const closeAll = () => {
    setEditing(null)
    setConfirming(null)
  }

  return (
    <section className="pf">
      <div className="pf-head">
        <h2 className="pf-h">身体档案</h2>
        <p className="pf-sub">
          这一家人的身体状况、目标与忌口。每一轮的推荐都以这些条件为准。
        </p>
      </div>

      {active && (
        <p className="pf-active">
          当前生效：<b>{active.name}</b>
          <span className="pf-active-note">下一个请求按他的条件生成方案</span>
        </p>
      )}

      <ul className="pf-list">
        {family.members.map((m) => {
          const p = m.profile
          const on = family.active_id === m.id
          const isEditing = editing === m.id
          const isConfirming = confirming === m.id
          // 每一行都先算好再决定画不画：值为空就整行不出现。
          const rows: Array<[string, string | null]> = [
            ['身体状况', line(p.conditions)],
            ['目标', line(p.goal)],
            ['过敏原', line(p.allergens)],
            ['医嘱限制', line(p.restricts)],
            ['不喜欢的食物', line(p.dislikes)],
            ['身体数据', bodyLine(p.basic)],
            ['口味备注', line(p.taste_notes)],
          ]
          const shown = rows.filter(([, v]) => v !== null) as Array<[string, string]>

          return (
            <li key={m.id} className="pf-card" data-on={on}>
              <div className="pf-card-head">
                <span className="pf-name">{m.name}</span>
                {on && <span className="pf-badge">当前生效</span>}

                <span className="pf-ops">
                  {!on && (
                    <button
                      type="button"
                      className="pf-pick"
                      disabled={switching !== null || busy}
                      onClick={() => onPick(m.id)}
                    >
                      {switching === m.id ? '切换中…' : '设为当前'}
                    </button>
                  )}
                  {!isEditing && (
                    <button
                      type="button"
                      className="pf-act"
                      disabled={busy}
                      onClick={() => {
                        setConfirming(null)
                        setEditing(m.id)
                      }}
                    >
                      编辑
                    </button>
                  )}
                  {/* 只剩一人时后端会拒（至少保留一位），所以这里先禁用而不是让它失败。 */}
                  <button
                    type="button"
                    className="pf-act pf-act-danger"
                    disabled={busy || onlyOne}
                    title={onlyOne ? '至少保留一位家庭成员' : undefined}
                    onClick={() => {
                      setEditing(null)
                      setConfirming(isConfirming ? null : m.id)
                    }}
                  >
                    删除
                  </button>
                </span>
              </div>

              {isConfirming && (
                <div className="pf-confirm" role="alert">
                  <span className="pf-confirm-q">
                    删除「{m.name}」？{on && '他正生效，删掉后会自动切到剩下的第一位。'}
                  </span>
                  <button
                    type="button"
                    className="pf-danger"
                    disabled={busy}
                    onClick={() => {
                      setConfirming(null)
                      onDelete(m.id)
                    }}
                  >
                    {busy ? '删除中…' : '确认删除'}
                  </button>
                  <button type="button" className="pf-act" onClick={() => setConfirming(null)} disabled={busy}>
                    取消
                  </button>
                </div>
              )}

              {isEditing ? (
                <MemberForm
                  initial={toInput(m)}
                  busy={busy}
                  onCancel={closeAll}
                  onSubmit={(input) => {
                    onUpdate(m.id, input)
                    setEditing(null)
                  }}
                />
              ) : shown.length > 0 ? (
                <dl className="pf-rows">
                  {shown.map(([label, value]) => (
                    <div className="pf-row" key={label}>
                      <dt className="pf-label">{label}</dt>
                      <dd className="pf-value">{value}</dd>
                    </div>
                  ))}
                </dl>
              ) : (
                <p className="pf-bare">这位成员还没有登记任何条件。</p>
              )}
            </li>
          )
        })}
      </ul>

      {/* 新增入口。满 8 人时禁用（后端会 400），并说明为什么。 */}
      {editing === 'new' ? (
        <div className="pf-newbox">
          <MemberForm
            initial={null}
            busy={busy}
            onCancel={closeAll}
            onSubmit={(input) => {
              onAdd(input)
              setEditing(null)
            }}
          />
        </div>
      ) : (
        <div className="pf-add">
          <button type="button" className="pf-add-btn" disabled={busy || full} onClick={() => setEditing('new')}>
            ＋ 添加成员
          </button>
          {full && <span className="pf-add-hint">最多 8 位家庭成员</span>}
        </div>
      )}

      {/* 家庭共享项：后端没有"家户级忌口"字段，所以它只能是**每个人都忌**的交集
          （推导在 familyFacts 里，这里不重算）。一个人忌的不算全家的，所以
          单人家庭永远不会出现这一块。 */}
      {shared && (
        <div className="pf-shared">
          <span className="pf-shared-label">全家都不吃</span>
          <span className="pf-shared-body">{shared.replace(/^共同忌口\s*/, '')}</span>
          <span className="pf-shared-note">推荐与采购会整体避开这几样</span>
        </div>
      )}

      {(writeErr || switchErr) && (
        <p className="pf-err" role="alert">
          {writeErr ?? switchErr}
        </p>
      )}

      <p className="pf-note">
        切换只影响下一个请求。正在等待中的那一轮仍按启动时的档案跑完，不会中途换人。
      </p>
    </section>
  )
}
