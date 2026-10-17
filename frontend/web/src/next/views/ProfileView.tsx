import { useState } from 'react'
import type { MemberInput } from '../../api/client.ts'
import { addFridge, setFridge } from '../../api/client.ts'
import { FridgePhotoButton } from '../blocks/FridgePhotoButton.tsx'
import { MemberForm } from '../blocks/MemberForm.tsx'
import { bmiFact, progressLine } from '../data/fieldFacts.ts'
import { memberBodyExtras, notifyFridgeChanged, useHousehold, type FamilyData } from '../data/household.ts'

/**
 * 「身体档案」房间（2026-10-05 落地；2026-10-11 按 v5 设计稿补齐版式）。
 *
 * 这一间回答的是用户原话的那个问题：「我建立的家庭画像不知道从哪个入口进入，
 * 也不知道如何增加新家庭人员和减家庭成员」。此前成员只出现在 Shell 的抽屉里，
 * 抽屉又只能**切换**，看不到一个人的全貌，更没有任何增删改——而旧前端
 * components/FamilyPanel.tsx 其实早就做完了这套。
 *
 * 三条纪律：
 *   1 档案本身是 props-only，这里**不再拉第二份 family**。档案由 app 用 useHousehold
 *     读一次再传进来，与起手页、等待页读的是同一份——各自去拉会让三页在档案刚改过的
 *     一瞬间显示不同的人（AskView 的文件头已经为此写过一次理由，这里是同一个理由）。
 *     增删改也一样：动作由上层传入，本组件只负责"什么时候请求这一次动作"。
 *   2 角色的**数值类**字段里，「目标达成率 / 目标进度百分比」后端仍没有字段；
 *     而孕周 / 体脂率 / 成员营养进度（2026-10-11 后端新增）有的有真值。有真值就
 *     显示真值，null 一律显示中性占位——绝不写一个像样的数字。写假的孕周或
 *     体脂率等于造假健康信息，这是红线，比"少画一块"严重得多。
 *   3 能算的就照实算（BMI 由身高体重得出），算不出的（缺体重）画「—」而不是 0。
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
/** 数值位的中性占位。用一个字面「待补」而不是 0 或一个像样的数字：
 *  0 会被当成"真的是零"，数字会被当成"真的是这个值"——两者在健康信息上都是撒谎。 */
const TODO = '待补'
/** 档案里确实没登记这一项时画它（与 TODO 区分：一个是"后端没有这个字段"，
 *  一个是"这一项用户没填"）。 */
const NIL = '—'

/** 一个成员 → 可提交的 MemberInput。编辑时要原样带上所有字段，不能只带页面上显示的那些。
 *  （孕周/体脂率也带上：PUT 是整成员覆盖，漏一个就是静默清空。） */
function toInput(m: FamilyData['members'][number]): MemberInput {
  const p = m.profile
  const { gestationalWeeks, bodyFatRate } = memberBodyExtras(m)
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
        gestational_weeks: gestationalWeeks,
        body_fat_rate: bodyFatRate,
      },
    },
  }
}

/** 编辑提交前，把 MemberForm 表里**没有输入框**的两个字段从原成员上原样带回。
 *
 *  必需的原因：PUT 是整成员覆盖，而 MemberForm 的 toMemberInput 是**重建**一份
 *  profile（只有表单里列出的那些字段）。不合并的话，用户改一次「目标」就会把
 *  之前录入的孕周/体脂率静默抹成 null——数据在，页面却显示「待补」，而且用户
 *  根本没动过那一栏。这一刀只在提交口补字段，不新增输入框（版式有另一条线在动）。 */
function withBodyFields(input: MemberInput, m: FamilyData['members'][number]): MemberInput {
  const { gestationalWeeks, bodyFatRate } = memberBodyExtras(m)
  return {
    ...input,
    profile: {
      ...input.profile,
      basic: {
        ...input.profile.basic,
        gestational_weeks: gestationalWeeks,
        body_fat_rate: bodyFatRate,
      },
    },
  }
}

/** 一个成员卡里的「基础数据」三列（照稿 x=344/452/600 的三列位置）。
 *
 *  照稿只有三列：体重 / BMI /（孕妇的）孕周。本地多出一个设计稿没有的麻烦：
 *  「小美」没有登记体重，而档案里**身高与年龄**原本是画在事实行「身体数据」里的
 *  （`176cm · 63kg · 19 岁`）。把那一行换成这个块以后，身高与年龄就没有地方体现了
 *  ——那是档案里的真数据，不能因为改版就让它消失。所以这里多起一列「身高」，
 *  年龄跟在性别那一格下面当第二行小字；孕周仍只对孕妇画。
 *
 *  孕周是**孕妇独有**的列，值取后端 basic.gestational_weeks（用户自己录的）：
 *  有值写值，null 写「待补」。体脂率（basic.body_fat_rate）**恒起一列**（2026-10-12 改：
 *  原先"只有真值时才起"，验收口径要求槽要看得见——没有真值写「待补」）。列数由内联的
 *  gridTemplateColumns 跟着实际列数走，profile.css 也明确"只定形制、不写死列数"，
 *  所以多一列是均分变窄，不会把值挤出卡片（1366 实测 .pf-basic-grid 341.25px / 5 列）。
 *
 *  BMI 槽的**签**（2026-10-12 修）：原先无论算不算得出，槽里恒挂一枚「待补」小签，
 *  于是「我」渲染成 `BMI 20.3 待补`——真值被自己的签说成占位。现在只有算不出
 *  （缺身高或体重）时才画「待补」，且缺哪一项由小注点名；算得出就只给值。
 *  保持这条：**值是真的时候，槽里不许出现任何占位字样**。 */
function BasicData({ m }: { m: FamilyData['members'][number] }) {
  const b = m.profile.basic
  const pregnant = m.profile.conditions.some((c) => c.includes('孕'))
  const { gestationalWeeks, bodyFatRate, nutrition } = memberBodyExtras(m)
  // 主值那一格"登记了什么就说什么"：有体重说体重，没有体重但登记了性别就说性别
  // （沿用 SEX_LABEL 的"认不出的值原样保留"口径），都没有才画 NIL。
  // 绝不能把标签留在「体重」而值写「女」，那是拿性别冒充体重；也绝不借身高、年龄来凑。
  const sex = b?.sex ? (SEX_LABEL[b.sex] ?? b.sex) : null
  const age = b?.age ? `${b.age} 岁` : null
  // BMI 由身高体重真算（推导在 data/fieldFacts.ts 的 bmiFact，有 node --test 钉着）。
  // 算得出就只给值，**不挂任何签**：2026-10-12 实测页面把「我」的真值渲染成了
  // `BMI 20.3 待补`——真值旁边挂占位签，等于把真数说成假的。
  const bmi = bmiFact(b)
  const mainCol: Array<[string, string, string | null]> = b?.weight_kg
    ? [['体重', `${b.weight_kg}kg`, age]]
    : sex
      ? [['性别', sex, age]]
      : [['体重', NIL, age]]

  const cols: Array<[string, string, string | null]> = [
    ...mainCol,
    ['身高', b?.height_cm ? `${b.height_cm}cm` : NIL, null],
    // BMI 的位置恒在孕周之前（照稿的列序）。算不出来才画「待补」——
    // 顺序不能因为"算不出来"就变，否则两张卡的列序会不一致。缺哪一项由下面
    // 那行小注说清（不是只丢一个占位符）。
    ['BMI', bmi.value ?? TODO, null],
  ]
  if (pregnant) cols.push(['孕周', gestationalWeeks === null ? TODO : `${gestationalWeeks} 周`, null])
  // 体脂率槽**恒在**（2026-10-12 用户验收口径：槽要看得见，没有真值写「待补」）。
  // 原先是"只有真值时才起这一列"，理由是怕多一列改掉成员卡的列数；但列数由下面
  // 内联的 gridTemplateColumns 跟着 cols.length 走、不写死（profile.css 也明确
  // "只定形制、不写死列数"），所以加一列不会把值挤出卡片——而"看不见的槽"
  // 恰恰让人分不清是没登记还是没画。
  cols.push(['体脂率', bodyFatRate === null ? TODO : `${bodyFatRate}%`, null])

  // 成员营养进度（后端 profile.nutrition_progress）：有真值就报真值 + 分子分母，
  // 让"这几成"能对上账；null（判据未定 / 该成员本周没有已记录餐次）就照实说未给出。
  // 文案推导在 data/fieldFacts.ts（纯函数，有 node --test 兜着）。
  const progressNote = progressLine(nutrition)

  return (
    <section className="pf-basic">
      <h4 className="pf-blk-h">基础数据</h4>
      <dl className="pf-basic-grid" style={{ gridTemplateColumns: `repeat(${cols.length}, minmax(0, 1fr))` }}>
        {cols.map(([label, value, sub]) => (
          <div className="pf-basic-col" key={label}>
            <dt className="pf-basic-k">{label}</dt>
            <dd className="pf-basic-v" data-todo={value === TODO ? 'true' : undefined}>
              {/* 值单独一个 span：dd 是 flex，直接放文本节点的话它与后面那个小签
                  之间的空白会被 flex 吃掉，实测渲染成「—待补」粘在一起。 */}
              <span className="pf-basic-num">{value}</span>
            </dd>
            {sub && <span className="pf-basic-sub">{sub}</span>}
          </div>
        ))}
      </dl>
      {/* 占位声明必须逐卡说，不能一句话挂在两张卡上：
          实测「我」的 BMI 是算得出来的真值（63kg/176cm → 20.3），
          把它一起说成占位就是本页最不该犯的那种错——把真数说成假的。
          缺哪一项也在这里点名（"待补"两个字本身不构成解释）。 */}
      <p className="pf-mdnote">
        {bmi.missing === null ? '' : `BMI 因缺${bmi.missing}暂算不出；`}
        {pregnant && gestationalWeeks === null ? '孕周尚未登记，数值位为占位；' : ''}
        {bodyFatRate === null ? '体脂率尚未登记，数值位为占位；' : ''}
        各项目标进度百分比后端暂未提供，数值位为占位。
        {` ${progressNote}`}
      </p>
    </section>
  )
}

/** 「目标与进度」（照稿：小标题 + 三条「名称 + 进度条 + 百分比」）。
 *  三条的名称随成员的目标变：孕妇走孕期口径，其余走增肌口径（稿上就是这个分法）。
 *  百分比后端没有，所以轨道照画、填充宽度留空——填一个 70% 就是在编造进度。 */
function GoalProgress({ m }: { m: FamilyData['members'][number] }) {
  const pregnant = m.profile.conditions.some((c) => c.includes('孕'))
  const rows = pregnant
    ? ['体重增长', '营养均衡', '胎儿发育']
    : ['体重增长', '肌肉量提升', '体脂率控制']

  return (
    <section className="pf-goal">
      <h4 className="pf-blk-h">{pregnant ? '孕期目标与进度' : '增肌目标与进度'}</h4>
      <ul className="pf-goal-list">
        {rows.map((r) => (
          <li className="pf-goal-item" key={r}>
            <span className="pf-goal-k">{r}</span>
            {/* 空轨道 = 后端无百分比。aria-hidden：它不是"0%"这个事实，读屏也不该读成 0。 */}
            <span className="pf-bar" aria-hidden="true" />
            <span className="pf-goal-v">{TODO}</span>
          </li>
        ))}
      </ul>
    </section>
  )
}

export function ProfileView({
  family,
  shared,
  readErr,
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
  /** 最近一次读取 /api/profile 失败的原因（useHousehold().readError）。
   *  只用来把"读失败"与"家里真没人"分开说，绝不参与画档案本身。 */
  readErr: string | null
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

  /**
   * 冰箱现有食材（稿上的「冰箱现有食材」卡）。
   *
   * 为什么这里会再起一个 useHousehold：Shell 已经把 family 传下来了，但它**没有**
   * 传 fridge，而 Shell.tsx 这一轮正在被同事改动、不许我碰。所以这里只借这个钩子取
   * 冰箱，family 仍以上面那个 prop 为准（解构里刻意不取 family，避免出现第二份档案）。
   * 代价是多一次 GET /api/fridge；冰箱与成员无关、不会因为一次写入而过期，
   * 所以这点重复读不会造成"同一份数据两个值"。
   */
  const { fridge } = useHousehold()

  /* ── 冰箱的写路径（2026-10-14 接线）──────────────────────────────────
   * 后端早就有 `/fridge/set`（覆盖）与 `/fridge/add`（补货），新版前端此前
   * **一次都没调过**——冰箱只能读。于是"我买了几样菜"在界面上无路可走，
   * 而冰箱是推荐的第一号输入（等待页那句「冰箱里只有鸡蛋和西兰花」就是它）。
   *
   * 三条口径：
   *   1 两个动作直接对应后端语义：删一样 = 传"剩下那几样"（后端把这次没出现的
   *     旧项记成「吃掉/用掉」，见 api/routes/fridge_route.py 的 set_fridge）；
   *     补货 = 追加去重。**没有**单独的删除端点，也不需要。
   *   2 成败以后端返回值为准：成功才广播 notifyFridgeChanged()，让另外三个
   *     读取方（首屏、等待页、结果页右栏）一起刷新；失败把原因如实放在块内，
   *     不把"没写成的"当成写成了。
   *   3 写入期间禁用：连点会发出两次 set，而后一次以它自己读到的旧清单为基准，
   *     后一次覆盖前一次——用户看到的是"点了两下，少了两样"。 */
  const [fridgeInput, setFridgeInput] = useState('')
  const [fridgeBusy, setFridgeBusy] = useState(false)
  const [fridgeErr, setFridgeErr] = useState<string | null>(null)

  const writeFridge = async (act: () => Promise<unknown>) => {
    if (fridgeBusy) return
    setFridgeBusy(true)
    setFridgeErr(null)
    try {
      await act()
      notifyFridgeChanged()
    } catch (err) {
      setFridgeErr(err instanceof Error ? err.message : String(err))
    } finally {
      setFridgeBusy(false)
    }
  }

  /** 记为吃完了：传"剩下的那几样"，后端把差额记成消耗。 */
  const eatItem = (it: string) => {
    void writeFridge(() => setFridge(fridge.filter((x) => x !== it)))
  }

  /** 补货：用户原样输入，切分交给后端——前端再切一份必然与后端分叉。 */
  const restock = () => {
    const text = fridgeInput.trim()
    if (text === '') return
    void writeFridge(async () => {
      await addFridge(text)
      // 只有真写成功才清空输入框；失败时用户不必重打一遍。
      setFridgeInput('')
    })
  }

  if (!family || family.members.length === 0) {
    return (
      <section className="pf">
        <h2 className="pf-h">身体档案</h2>
        {/* 兜底文案照旧（读不到就不猜家里有谁）；只多了半句失败原因。
            没有它的话，"后端正忙/网络断了"和"家里真没建档"在页面上长得一模一样，
            而这两种处境该做的事完全不同——一个是等（这里已在自动重读），一个是去建档。 */}
        <p className="pf-empty">
          档案还没有读出来，先不猜家里有谁。
          {readErr ? `（读取失败：${readErr}；正在自动重读，读到即显示）` : ''}
        </p>
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
      <div className="pf-main">
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

      <section className="pf-sec">
      <h3 className="pf-sec-h">家庭成员信息</h3>
      <ul className="pf-list">
        {family.members.map((m) => {
          const p = m.profile
          const on = family.active_id === m.id
          const isEditing = editing === m.id
          const isConfirming = confirming === m.id
          // 每一行都先算好再决定画不画：值为空就整行不出现。
          // 身体数据（身高/体重/年龄）已移到下面「基础数据」三列块，见 BasicData。
          const rows: Array<[string, string | null]> = [
            ['身体状况', line(p.conditions)],
            ['目标', line(p.goal)],
            ['过敏原', line(p.allergens)],
            ['医嘱限制', line(p.restricts)],
            ['不喜欢的食物', line(p.dislikes)],
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
                    onUpdate(m.id, withBodyFields(input, m))
                    setEditing(null)
                  }}
                />
              ) : (
                <>
                  <BasicData m={m} />
                  {shown.length > 0 && (
                    <dl className="pf-rows">
                      {shown.map(([label, value]) => (
                        <div className="pf-row" key={label}>
                          <dt className="pf-label">{label}</dt>
                          <dd className="pf-value">{value}</dd>
                        </div>
                      ))}
                    </dl>
                  )}
                  <GoalProgress m={m} />
                </>
              )}
            </li>
          )
        })}
      </ul>
      </section>

      {/* 新增入口。满 8 人时禁用（后端会 400），并说明为什么。
          放在成员卡同一行的后半格（稿上第二行右侧那格），窄屏回落成一列。 */}
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

      {/* 稿上第二行那两块：左「共同忌口与偏好」、右「冰箱现有食材」。
          包在一层两列容器里显式横排，不依赖 .pf-main 的自动排布（见 profile.css 的说明）。

          共同忌口：后端没有"家户级忌口"字段，所以它只能是**每个人都忌**的交集
          （推导在 familyFacts 里，这里不重算）。一个人忌的不算全家的，所以
          单人家庭永远不会出现这一块。

          冰箱现有食材：唯一一块**纯真实数据**的新块（useHousehold().fridge）。
          稿上每样配一枚成品图，本地没有对应素材，所以只上字——不猜图、不放空框。 */}
      <div className="pf-pair">
      {shared && (
        <section className="pf-sec pf-sec--shared">
        <h3 className="pf-sec-h">共同忌口与偏好</h3>
        <div className="pf-shared">
          <span className="pf-shared-label">全家都不吃</span>
          <span className="pf-shared-body">{shared.replace(/^共同忌口\s*/, '')}</span>
          <span className="pf-shared-note">推荐与采购会整体避开这几样</span>
        </div>
        </section>
      )}

      <section className="pf-sec pf-sec--fridge">
      <h3 className="pf-sec-h">冰箱现有食材</h3>
      {fridge.length > 0 ? (
        <ul className="pf-fridge">
          {fridge.map((it) => (
            <li className="pf-fridge-item" key={it}>
              <span className="pf-fridge-ic" aria-hidden="true" />
              <span className="pf-fridge-n">{it}</span>
              {/* 记为吃完了：后端把这次没传的项记成消耗，所以「删」= 传"剩下的"。
                  清单清空也是 set 的合法输入（全都吃完了）。 */}
              <button
                type="button"
                className="pf-fridge-del"
                onClick={() => eatItem(it)}
                disabled={fridgeBusy}
                title={`把「${it}」记为吃完了`}
                aria-label={`把「${it}」记为吃完了`}
              >
                ×
              </button>
            </li>
          ))}
        </ul>
      ) : (
        <p className="pf-bare">冰箱这一侧还没读出食材。</p>
      )}

      {/* 补货：一块最朴素的输入行。用户怎么说话就怎么打
          （「鸡蛋、一把青菜、200g 牛肉」都行），数量单位与「家里还有」这类前缀
          由后端清掉——这里不做任何解析，解析规则只留后端一份。 */}
      <form
        className="pf-fridge-add"
        onSubmit={(e) => {
          e.preventDefault()
          restock()
        }}
      >
        <FridgePhotoButton onRecognized={(text) => setFridgeInput(text)} />
        <input
          className="pf-fridge-input"
          value={fridgeInput}
          onChange={(e) => setFridgeInput(e.target.value)}
          placeholder="补货：鸡蛋、一把青菜…"
          disabled={fridgeBusy}
          aria-label="补货：输入要加进冰箱的食材"
        />
        <button
          type="submit"
          className="pf-fridge-btn"
          disabled={fridgeBusy || fridgeInput.trim() === ''}
        >
          {fridgeBusy ? '保存中…' : '存进冰箱'}
        </button>
      </form>

      {fridgeErr && (
        <p className="pf-err" role="alert">
          冰箱没存上：{fridgeErr}
        </p>
      )}
      </section>
      </div>

      {(writeErr || switchErr) && (
        <p className="pf-err" role="alert">
          {writeErr ?? switchErr}
        </p>
      )}

      <p className="pf-note">
        切换只影响下一个请求。正在等待中的那一轮仍按启动时的档案跑完，不会中途换人。
      </p>
      </div>

      {/* 右侧信息栏（照稿的两列版式）。「家庭概览」是已定稿的三块之一，保持原样；
          下面三块照 v5 稿补进来：营养目标达成、今日饮食建议、好好吃饭。 */}
      <aside className="pf-rail">
        <section className="pf-rail-card">
          <h3 className="pf-rail-h">家庭概览</h3>
          <ul className="pf-rail-list">
            {family.members.map((m) => (
              <li key={m.id} className="pf-rail-mem">
                <span className="pf-rail-name">{m.name}</span>
                <span className="pf-rail-tag">
                  {line(m.profile.conditions) ?? line(m.profile.goal) ?? '未登记条件'}
                </span>
                {family.active_id === m.id && <span className="pf-rail-on">当前生效</span>}
              </li>
            ))}
          </ul>
        </section>

        {/* 营养目标达成：稿上是 2×2 四个圆环。后端没有达成率，所以环里不写百分比——
            环只画轨道，圆心写中性占位。四个维度名照稿保留。 */}
        <section className="pf-rail-card">
          <h3 className="pf-rail-h">营养目标达成情况</h3>
          <ul className="pf-ring-grid">
            {['蛋白质', '碳水化合物', '脂肪', '维生素'].map((k) => (
              <li className="pf-ring-item" key={k}>
                <span className="pf-ring-k">{k}</span>
                <span className="pf-ring" aria-hidden="true">
                  <span className="pf-ring-hole">{TODO}</span>
                </span>
              </li>
            ))}
          </ul>
        </section>

        {/* 今日饮食建议：稿上是三条无勾选的要点。后端本页没有"今日建议"字段，
            所以只保留三条要点槽位的形制，内文为中性占位——绝不编一条看似专业的建议
            （"多吃优质蛋白"这种话在这里会被当成系统给出的医嘱）。 */}
        <section className="pf-rail-card">
          <h3 className="pf-rail-h">今日饮食建议</h3>
          <ul className="pf-tips">
            {[0, 1, 2].map((i) => (
              <li className="pf-tip" key={i}>
                <span className="pf-tip-dot" aria-hidden="true" />
                <span className="pf-tip-v">{TODO}</span>
              </li>
            ))}
          </ul>
          <p className="pf-rail-body">建议内容后端暂未提供，先按稿留出三条的位置。</p>
        </section>

        {/* 好好吃饭：稿上右栏最下面那一条寄语。它是固定文案（不是数据），照稿落。 */}
        <section className="pf-rail-card">
          <h3 className="pf-rail-h">好好吃饭</h3>
          <p className="pf-rail-body">是对身体最温柔的照顾。</p>
        </section>
      </aside>
    </section>
  )
}
