/**
 * 家庭档案与冰箱：起手页和等待页**共用**的同一份推导。
 *
 * 为什么要有这个文件：这两页都要回答"这顿饭是按什么定的"，
 * 而以前只有等待页在做（WaitCard 里的私有函数）。起手页拿到首屏时若要再说一遍，
 * 抄一份就必然抄歪——两页对同一个档案给出不同措辞，是最容易被用户抓住的不一致。
 * 所以推导只留一份，两个页面都从这里取。
 *
 * 两条纪律（从 WaitCard 的旧注释继承下来，不能丢）：
 *   1 只读，不写。读失败就什么都不说——绝不替档案的缺席编造内容。
 *   2 只返回值本身，标签由版式给出。也绝不写「照顾全家」这类话——
 *     那是在替系统承诺它没承诺过的事。
 */

import { useEffect, useState } from 'react'
import { fetchFamily, fetchFridge } from '../../api/client.ts'
import { readRetryDelayMs } from './retry.ts'
import { dislikesIntersection } from './fieldFacts.ts'

/** 成员切换广播事件名（Shell 抽屉切换成功后发出，useHousehold({live:true}) 订阅）。 */
const MEMBER_SWITCHED = 'member-switched'

/** 冰箱写入广播事件名（档案页「吃完了 / 补货」成功后发出，2026-10-14）。
 *
 *  为什么需要它：冰箱有**四个**读取方（首屏右栏、等待页的已知输入、结果页右栏、
 *  档案页自己），而写入只发生在档案页。没有广播的话，写完之后别的页面还挂着旧清单，
 *  同一个"冰箱里有什么"会在四个地方各说各话。
 *
 *  与 MEMBER_SWITCHED **分开**而不是复用：成员切换要重读 family、冰箱写入要重读 fridge，
 *  合成一个事件会让每次切换成员都白读一次冰箱（而冰箱与成员无关）。 */
const FRIDGE_CHANGED = 'fridge-changed'

/** 切换成功的一方调用它通知全局重读档案；写路径的成败以后端返回值为准，
 *  没成功不许广播——否则各处会重读到没变的档案，用户以为切换没生效。 */
export function notifyMemberSwitched(): void {
  window.dispatchEvent(new Event(MEMBER_SWITCHED))
}

/** 冰箱写入成功的一方调用它通知全局重读冰箱（口径同 notifyMemberSwitched：
 *  没写成功不许广播，否则用户会以为自己的操作没生效）。 */
export function notifyFridgeChanged(): void {
  window.dispatchEvent(new Event(FRIDGE_CHANGED))
}

export type FamilyData = Awaited<ReturnType<typeof fetchFamily>>

/** 家庭档案里"已登记的事实"。
 *  一人一行，不压成逗号串——「小美 · 孕妇」与「我 · 增肌 · 176cm · 63kg」
 *  这样排比参数表好扫读，而且看得出哪一条属于谁。 */
export function familyFacts(
  family: FamilyData | null,
): { members: string[]; shared: string | null } | null {
  if (!family || family.members.length === 0) return null
  const members = family.members.map((m) => {
    const p = m.profile
    const bits: string[] = [...p.conditions]
    if (p.goal) bits.push(p.goal)
    // 身高体重挂在 profile.basic 上，且只在两者都有时才写，
    // 避免出现「176cm · —」这种半截话。
    const b = p.basic
    if (b.height_cm && b.weight_kg) bits.push(`${b.height_cm}cm · ${b.weight_kg}kg`)
    return bits.length > 0 ? `${m.name} · ${bits.join(' · ')}` : m.name
  })
  // 「共同忌口」的**取值**与判据搬在 sharedDislike 里（见那个函数上方的注释），
  // 这里只负责把它拼成 familyFacts 要的链式排印形态——等待页要的是值，不是这一串。
  const value = sharedDislike(family)
  return { members, shared: value ? `共同忌口 ${value}` : null }
}

/** 等待页「家庭成员与忌口」那块瓦片墙的结构化取值（2026-10-12 等待页改版）。
 *
 *  为什么不再复用 familyFacts 的字符串：那一版把一个人压成「小美 · 孕妇 ·
 *  176cm · 63kg」一整行，是**链式排印**的料；新稿把一个人画成一块瓦片——
 *  名字一档、标签一档，两档字号不同。从拼接好的字符串再 split 回来，等于
 *  让版式去猜数据的边界，档案里出现一个带「·」的标签就会当场裂成两半。
 *  所以这里与 familyFacts 一样只做**取值**，标签由版式给出（同一份纪律）。
 *
 *  tag 取第一个标签而不是全部：瓦片只有一行小字的位置，全塞进去会溢出。
 *  完整的几条仍在「身体档案」房，这里不是它的唯一出口。 */
export function familyTiles(family: FamilyData | null): { name: string; tag: string | null }[] {
  if (!family) return []
  return family.members.map((m) => {
    const bits: string[] = [...m.profile.conditions]
    if (m.profile.goal) bits.push(m.profile.goal)
    return { name: m.name, tag: bits.length > 0 ? bits[0] : null }
  })
}

/** 共同忌口的**值**（交集本身），等待页那块瓦片与 familyFacts 都从这里取；
 *  「共同忌口」四个字由版式/调用方给出，这里只交值。
 *
 *  必须是**交集**，不是并集（2026-10-05 修正）。这里原来在 familyFacts 里写的是
 *  flatMap 去重——那是并集，却挂着"共同"两个字。当前真实档案两人恰好都忌芹菜，
 *  并集与交集相等，所以这个错一直没露出来；一旦只有一个人忌香菜，页面就会写着
 *  「共同忌口 香菜」，把个人忌口说成全家的。后端没有"家户级忌口"这种字段，
 *  全家都忌只能从"每个人都忌"推出来——那才是交集。
 *
 *  单人家庭不产生共同项：一个人的忌口是他自己的，不是全家的。所以阈值是 ≥2 人。 */
export function sharedDislike(family: FamilyData | null): string | null {
  if (!family || family.members.length < 2) return null
  // 优先用后端算好的值（2026-10-14 起 `/api/profile` 返回 `shared_dislikes`）：
  // 口径从此只有一处实现。后端字段缺席（旧响应 / 接口异常）时才本地重算，
  // 且**仍是交集**——绝不退回并集。
  const shared = family.shared_dislikes ?? dislikesIntersection(family.members)
  return shared.length > 0 ? shared.join('、') : null
}

/** 成员身上三个新数值（后端 2026-10-11 新增字段）的**原样读取**。
 *
 *  实现搬在 ./fieldFacts.ts（纯模块，可被 node --test 直接测）：本文件头部 import
 *  了 react 与 api/client.ts（后者读 import.meta.env），在 node 里加载不起来，
 *  把"没有真值就落 null、绝不落成 0/估算值"的判定留在这里等于测不到。
 *  这里保留这个出口，是因为档案页与起手页读档案都从本模块进。
 *
 *  只做"取出来"，不做任何换算：孕周不反推预产期、体脂率不由 BMI 估、
 *  营养进度不由"绿灯占比"顶。 */
export { memberBodyExtras } from './fieldFacts.ts'

/** 只读取一次的家庭档案 + 冰箱。任何一侧失败都退化成 null / 空数组，不抛错、不编造。
 *
 * `live`（2026-10-01 抽屉切片新增）：
 *   · live=true（默认）——订阅成员切换广播。起手页用它：切换后「下一轮按谁推荐」
 *     必须立刻显示新的 active 成员，否则两处显示打架。
 *   · live=false（快照）——只读首载那一次。等待页用它：这一轮的档案在启动时
 *     就定死了（lave 契约：进行中的轮绑定启动时的成员快照），
 *     中途切换不该让正在等待的用户看到"它换人了"。
 *     （2026-10-12 补：live=false 也不订阅广播，但**同样享受失败退避重读**——
 *      快照的语义是"不跟着切换走"，不是"读失败就永远没有"。）
 */
export function useHousehold(opts?: { live?: boolean }): {
  family: FamilyData | null
  fridge: string[]
  activeName: string | null
  /** 最近一次读取失败的原因；读到成功即清成 null。
   *  存在的理由：把它和"档案里真的没有人"分开说——否则两种完全不同的处境
   *  （网络坏了 / 家里没建档）在页面上是同一句兜底文案，用户与排查者都分不出来。 */
  readError: string | null
  /** 写路径（增/改/删成员）成功后直接把这个新档案放进来。
   *  三个写端点都返回**整份**新 family，所以这里不必再拉一次 GET——
   *  重拉不仅多一次往返，还会在"后端已写、GET 未回"的窗口里让页面短暂显示旧档案。 */
  applyFamily: (f: FamilyData) => void
} {
  const live = opts?.live ?? true
  const [family, setFamily] = useState<FamilyData | null>(null)
  const [fridge, setFridge] = useState<string[]>([])
  const [readError, setReadError] = useState<string | null>(null)

  /* 读取必须会自愈（2026-10-12 修，见 ./retry.ts 头部那段实测）：
   * 这一读发生在页面加载那一刻，那一刻**未必**是后端可用的一刻。原先只有
   * `.catch(() => {})`——失败等价于"这份档案不存在"，而重读只挂在成员切换广播上，
   * 于是后端恢复后页面永远停在「档案还没有读出来」。
   *
   * 现在的三条口径：
   *   1 失败退避重读（1s→2s→…→30s，封顶不放弃），读到即停；
   *   2 读到过一次之后，后续任何失败都**不清空**已有档案（旧数据比空面板诚实）；
   *   3 失败原因记进 readError，成功时清掉——兜底文案因此能说清"为什么读不到"。 */
  useEffect(() => {
    let alive = true
    let familyTimer: ReturnType<typeof setTimeout> | null = null
    let fridgeTimer: ReturnType<typeof setTimeout> | null = null
    let familyAttempt = 0
    let fridgeAttempt = 0
    let familyInflight = false
    let fridgeInflight = false

    const readFamily = () => {
      if (!alive || familyInflight) return
      familyInflight = true
      fetchFamily()
        .then((f) => {
          if (!alive) return
          familyAttempt = 0
          setReadError(null)
          setFamily(f)
        })
        .catch((err: unknown) => {
          if (!alive) return
          setReadError(err instanceof Error ? err.message : String(err))
          familyTimer = setTimeout(readFamily, readRetryDelayMs(familyAttempt++))
        })
        .finally(() => {
          familyInflight = false
        })
    }

    /* 冰箱与档案在同一个故障窗口里一起失败（实测 load 期 6 次 /api/fridge 全是 502），
     * 所以它也要重读——否则档案回来了，本页脚下的「冰箱现有食材」还是空的。 */
    const readFridge = () => {
      if (!alive || fridgeInflight) return
      fridgeInflight = true
      fetchFridge()
        .then((items) => {
          if (!alive) return
          fridgeAttempt = 0
          setFridge(items)
        })
        .catch(() => {
          if (!alive) return
          fridgeTimer = setTimeout(readFridge, readRetryDelayMs(fridgeAttempt++))
        })
        .finally(() => {
          fridgeInflight = false
        })
    }

    readFamily()
    readFridge()

    // 成员切换的广播重读（只重读 family，冰箱与成员切换无关，不动它）。
    // 这是用户主动动作：立刻重读并把退避归零，不让他等下一次退避的 30s。
    const refetch = () => {
      if (!live) return
      familyAttempt = 0
      if (familyTimer !== null) {
        clearTimeout(familyTimer)
        familyTimer = null
      }
      readFamily()
    }
    window.addEventListener(MEMBER_SWITCHED, refetch)

    /* 冰箱写入的广播重读（2026-10-14 新增，配合档案页的「吃完了 / 补货」）。
     * 与成员切换同一条口径：用户主动动作 → 立刻重读、退避归零，不让他等 30s。
     * **同样受 live 门控**：等待页是快照，这一轮用的就是它启动时那份冰箱，
     * 中途改冰箱不该让正在等待的用户看到"它换了一批食材"
     * （与 family 的 live=false 同一条理由，见上面 live 那段的说明）。 */
    const refetchFridge = () => {
      if (!live) return
      fridgeAttempt = 0
      if (fridgeTimer !== null) {
        clearTimeout(fridgeTimer)
        fridgeTimer = null
      }
      readFridge()
    }
    window.addEventListener(FRIDGE_CHANGED, refetchFridge)

    return () => {
      alive = false
      if (familyTimer !== null) clearTimeout(familyTimer)
      if (fridgeTimer !== null) clearTimeout(fridgeTimer)
      window.removeEventListener(MEMBER_SWITCHED, refetch)
      window.removeEventListener(FRIDGE_CHANGED, refetchFridge)
    }
  }, [live])

  const activeName = family?.members.find((m) => m.id === family.active_id)?.name ?? null
  return { family, fridge, activeName, readError, applyFamily: setFamily }
}
