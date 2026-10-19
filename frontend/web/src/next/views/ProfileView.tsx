import { useCallback, useEffect, useState, type CSSProperties } from 'react'
import type { IntakeTips, MemberInput } from '../../api/client.ts'
import {
  addFridge,
  fetchIntakeTips,
  fetchNutritionCoverage,
  fetchWeightLog,
  postWeight,
  setFridge,
  type NutritionCoverage,
  type WeightSummary,
} from '../../api/client.ts'
import { FridgePhotoButton } from '../blocks/FridgePhotoButton.tsx'
import { MemberForm } from '../blocks/MemberForm.tsx'
import { bmiFact, progressLine, weightLine } from '../data/fieldFacts.ts'
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

/**
 * 右栏四个环的维度与显示名（2026-10-18）。
 *
 * 原稿是「蛋白质 / 碳水化合物 / 脂肪 / 维生素」，但后端给不出这四个达成率：
 * `meals.json` 的菜名没有重量，算不出摄入克数；`lights` 里实测只出现过
 * 「钠 / 糖 / 脂肪」三种灯，**没有蛋白质、碳水、维生素**。所以四个环改成
 * 能被原文证明的四维**覆盖度**，名字也必须跟着改——继续挂「碳水化合物」「维生素」
 * 的牌子而填别的东西，比留个「待补」更糟（用户会拿它当营养结论）。
 *
 * 「维生素」这一维被换掉的原因最硬：`data/china_food_components.csv` 里
 * 只有 `维生素Cmg`，没有 A/D/E/K/B 族，合不出"维生素"这一个百分比。
 */
const COVERAGE_RINGS = [
  { key: 'protein', label: '优质蛋白' },
  { key: 'staple', label: '主食' },
  { key: 'fat', label: '脂肪灯' },
  { key: 'produce', label: '蔬果' },
] as const
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
function BasicData({
  m,
  weight,
}: {
  m: FamilyData['members'][number]
  weight?: WeightSummary
}) {
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
  /**
   * 体重取值优先级：**体重记录的最新一条 → 档案里登记的 weight_kg**。
   *
   * 体重要靠日积月累才看得出趋势，用户记了新的就不该继续拿建档时的旧值算 BMI
   * （否则页面会出现"体重 65kg"旁边写着按 63kg 算出来的 BMI，两个数互相矛盾）。
   * 但从没记过的人不能因此变成"算不出"，所以要回落档案值。
   */
  const kg = weight?.latest?.weight_kg ?? b?.weight_kg ?? null
  // BMI 由身高体重真算（推导在 data/fieldFacts.ts 的 bmiFact，有 node --test 钉着）。
  // 算得出就只给值，**不挂任何签**：2026-10-12 实测页面把「我」的真值渲染成了
  // `BMI 20.3 待补`——真值旁边挂占位签，等于把真数说成假的。
  const bmi = bmiFact({ height_cm: b?.height_cm, weight_kg: kg })
  const mainCol: Array<[string, string, string | null]> = kg != null
    ? [['体重', `${kg}kg`, age]]
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
  // 体重记录那一行的话（三种状态都不写 0.0kg，推导见 fieldFacts.weightLine）。
  const weightNote = weightLine(weight)

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
        {weightNote}
        {` ${progressNote}`}
      </p>
    </section>
  )
}

/**
 * 成员卡内的「记录体重」（2026-10-18 新增）。**数据由用户自己录入** ——
 * 本页其它数字都来自档案或后端统计，只有这一项是用户主动交上来的。
 *
 * 三个刻意的取舍：
 *  - **保存后不本地拼值，而是回调 `onSaved` 重拉**：同一天重复记在后端是**覆盖**
 *    语义，本地往数组里 push 会把"今天已经记过"拼成两条，页面显示 2 条而后端只有 1 条。
 *  - **日期可改**：用户常常隔几天才想起来补记。如果只能记"今天"，
 *    「体重变化」永远算不出来（分母永远只有当天一条）。
 *  - **出错原样显示后端的话**：体重超范围、日期格式坏都是后端判的（400 + detail）。
 *    前端另编一句就会与真实规则分叉——后端改了边界，这里还在念旧的。
 */
function WeightEntry({
  name,
  summary,
  err,
  onSaved,
}: {
  name: string
  summary?: WeightSummary
  err: string
  onSaved: () => void
}) {
  const [value, setValue] = useState('')
  const [day, setDay] = useState('')
  const [saving, setSaving] = useState(false)
  const [msg, setMsg] = useState('')

  const submit = () => {
    const text = value.trim()
    if (!text) {
      setMsg('先填一个体重（kg）。')
      return
    }
    const kg = Number(text)
    if (!Number.isFinite(kg)) {
      setMsg('体重得是数字。')
      return
    }
    setSaving(true)
    setMsg('')
    postWeight(name, kg, day.trim() || undefined)
      .then(() => {
        setValue('')
        setMsg('已记录。')
        onSaved()
      })
      .catch((e) => setMsg(e instanceof Error ? e.message : '没能记上，稍后再试。'))
      .finally(() => setSaving(false))
  }

  const count = summary?.count ?? 0

  return (
    <div className="pf-weight">
      <div className="pf-weight-row">
        <input
          className="pf-weight-in"
          type="number"
          inputMode="decimal"
          step="0.1"
          min="20"
          max="400"
          placeholder="体重 kg"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') submit()
          }}
        />
        <input
          className="pf-weight-day"
          type="date"
          aria-label="记录日期"
          value={day}
          onChange={(e) => setDay(e.target.value)}
        />
        <button type="button" className="pf-act" onClick={submit} disabled={saving}>
          {saving ? '记录中…' : '记录体重'}
        </button>
      </div>
      {/* 空态说清怎么让它有值；有记录就报条数与最新一条的日期。
          这里**不写任何百分比、不做任何胖瘦评价**——它只陈述"记了几次"。 */}
      <p className="pf-weight-hint">
        {err
          ? err
          : count === 0
            ? '还没有记录。记一次，这里会显示最新体重并参与 BMI 计算。'
            : `已记录 ${count} 次，最新一条 ${summary?.latest?.date ?? NIL}。`}
      </p>
      {msg && (
        <p className="pf-weight-msg" role="status">
          {msg}
        </p>
      )}
    </div>
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

  /* ── 今日饮食建议（2026-10-19 接线）────────────────────────────────────
   * 后端 `GET /api/reports/intake-tips` 只取最近 6 餐、按**家庭整体**算：
   * 判据是「黄红灯占该指标样本的比例」，不是趋势——实测钠的黄红占比 61% 时
   * 三类趋势全是 improving，只看趋势会一言不发（详见 domain/intake_tips.py）。
   *
   * 口径与档案无关，所以这里自己拉一次，不占 useHousehold 的那份 family：
   * 建议看的是「这个家最近吃什么」，不是「当前生效成员是谁」。
   * 读失败不影响页面其余部分——这块降级成一句说明，档案仍可用。 */
  const [intake, setIntake] = useState<IntakeTips | null>(null)
  const [intakeErr, setIntakeErr] = useState<string | null>(null)

  useEffect(() => {
    let alive = true
    fetchIntakeTips()
      .then((res) => { if (alive) setIntake(res) })
      .catch((err) => {
        if (alive) setIntakeErr(err instanceof Error ? err.message : String(err))
      })
    return () => { alive = false }
  }, [])

  /* ── 本周四维营养覆盖度（2026-10-18 接线）────────────────────────────
   * 右栏「本周营养覆盖」那四个环的数据。此前是四个空环恒写「待补」：后端
   * 从来没有这个字段，前端照 v5 稿留了槽位（见本文件头注释第 2 条）。
   *
   * 口径是**覆盖度而不是达成率**：meals.json 的 dish 是自由文本菜名、没有任何
   * 重量，算不出摄入克数，所以「吃到目标的百分之几」在这个数据面上不存在；编一个
   * 默认份量去凑，在孕期成员身上就是错误医嘱。四个环报的是能被原文证明的事实——
   * 本周有几分之一的餐吃到了蛋白源 / 主食 / 蔬果，以及脂肪灯为绿的比例
   * （lights 里确实有脂肪灯，是唯一能取实值的一维）。完整论证见
   * storage/nutrition_coverage.py 与 api/client.ts 的 fetchNutritionCoverage。
   *
   * 不传成员名：后端按档案的 active_id 取，与右栏「当前生效」视角一致。
   * 切换生效成员后必须重取，所以依赖 active_id。
   */
  const [coverage, setCoverage] = useState<NutritionCoverage | null>(null)
  useEffect(() => {
    let alive = true
    fetchNutritionCoverage()
      .then((d) => {
        if (alive) setCoverage(d)
      })
      .catch(() => {
        if (alive) setCoverage(null)
      })
    return () => {
      alive = false
    }
  }, [family?.active_id])

  /* ── 体重记录（2026-10-18 接线）──────────────────────────────────────
   * 由用户在成员卡里自己录入（见 BasicData 里的「记录体重」）。**一次拉全家**：
   * 档案页一屏要画所有成员卡片，按人拉 N 次会读到不同时点的 weight_log.json，
   * 同屏出现互相矛盾的两个体重。
   *
   * 这份数据有两个用途：
   *  ① 覆盖「体重」显示位与 BMI 的输入值（优先级：记录最新一条 → 档案值）；
   *  ② 供小注说明变化量。**不判断这个变化正不正常**——孕期增重有医学推荐区间，
   *     但那是产检的事，详见 data/fieldFacts.ts 的 weightLine。
   */
  const [weights, setWeights] = useState<Record<string, WeightSummary>>({})
  const [weightErr, setWeightErr] = useState('')
  const reloadWeights = useCallback(() => {
    fetchWeightLog()
      .then((d) => {
        setWeights(d)
        setWeightErr('')
      })
      .catch(() => setWeightErr('体重记录没读出来。'))
  }, [])
  useEffect(() => {
    reloadWeights()
  }, [reloadWeights, family?.active_id])

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
                  <BasicData m={m} weight={weights[m.name]} />
                  <WeightEntry
                    name={m.name}
                    summary={weights[m.name]}
                    err={weightErr}
                    onSaved={reloadWeights}
                  />
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

        {/* 本周营养覆盖：2026-10-18 接上后端真数据。
            此前是四个恒写「待补」的空环（后端从来没有"达成率"这个字段，前端照
            v5 稿留槽位）。现在的口径是**覆盖度**——菜名原文能证明「这一餐有没有
            吃到蛋白源/主食/蔬果」，脂肪用现成的营养灯。分母为 0（本周还没记到
            这位成员的餐，例如孕期成员至今零归属）→ 仍写「待补」，绝不写 0%。 */}
        <section className="pf-rail-card">
          <h3 className="pf-rail-h">本周营养覆盖</h3>
          <ul className="pf-ring-grid">
            {COVERAGE_RINGS.map(({ key, label }) => {
              const dim = coverage?.[key]
              /* 先收窄成一个数再画：`has` 只保证「有值」，TS 不会据此把 dim.value
                 从 number|null 收窄，直接乘 100 会报 TS18047。 */
              const pct =
                dim && dim.value != null && dim.denominator > 0
                  ? Math.round(dim.value * 100)
                  : null
              const has = pct != null
              return (
                <li className="pf-ring-item" key={key}>
                  <span className="pf-ring-k">{label}</span>
                  <span
                    className="pf-ring"
                    aria-hidden="true"
                    /* 环的填充比例。无数据时给 0%（整圈轨道色）而不是省略：
                       省略会回落到 CSS 默认值，语义上变成"这个属性没人管"。 */
                    style={{ '--ring-p': has ? `${pct}%` : '0%' } as CSSProperties}
                  >
                    <span className="pf-ring-hole">
                      {has ? `${pct}%` : NIL}
                    </span>
                  </span>
                  <span className="pf-ring-sub">
                    {has && dim ? `${dim.numerator}/${dim.denominator} 餐` : '本周未记到餐次'}
                  </span>
                </li>
              )
            })}
          </ul>
          <p className="pf-rail-body">
            口径：按菜名判定这一周有几分之一的餐吃到了对应类别，「脂肪灯」取该餐营养灯为绿的比例。
            菜名里没有重量的餐算不出摄入量，所以这里给的是覆盖度而非达成率。
          </p>
        </section>

        {/* 今日饮食建议：稿上是三条无勾选的要点。内容来自后端按「最近 6 餐的
            钠/糖/脂肪灯色」算出的确定性建议，每条都能追到一个真实统计量。
            条数**不补位**——有几条说几条，凑不满三条就只显示两条（凑数等于编造）。 */}
        <section className="pf-rail-card">
          <h3 className="pf-rail-h">今日饮食建议</h3>
          {intake && intake.tips.length > 0 ? (
            <>
              <ul className="pf-tips">
                {intake.tips.map((tip, i) => (
                  <li className="pf-tip" key={`${tip.kind}-${tip.metric ?? 'x'}-${i}`}>
                    <span className="pf-tip-dot" aria-hidden="true" />
                    <span className="pf-tip-v">
                      {tip.text}
                      {tip.detail && <span className="pf-tip-detail">{tip.detail}</span>}
                    </span>
                  </li>
                ))}
              </ul>
              <p className="pf-rail-body">
                按最近 {intake.meals} 餐的钠、糖、脂肪统计，全家的账一起算。
              </p>
            </>
          ) : (
            <p className="pf-rail-body">
              {intakeErr
                ? `建议暂时读不出来：${intakeErr}`
                : '还没有饮食记录——聊一道菜，吃完自动记一笔，这里就有内容了。'}
            </p>
          )}
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
