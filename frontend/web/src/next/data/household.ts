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
  const dislikes = [...new Set(family.members.flatMap((m) => m.profile.dislikes))]
  return { members, shared: dislikes.length > 0 ? `共同忌口 ${dislikes.join('、')}` : null }
}

/** 只读取一次的家庭档案 + 冰箱。任何一侧失败都退化成 null / 空数组，不抛错、不编造。 */
export function useHousehold(): {
  family: FamilyData | null
  fridge: string[]
  activeName: string | null
} {
  const [family, setFamily] = useState<FamilyData | null>(null)
  const [fridge, setFridge] = useState<string[]>([])

  useEffect(() => {
    let alive = true
    fetchFamily()
      .then((f) => {
        if (alive) setFamily(f)
      })
      .catch(() => {})
    fetchFridge()
      .then((items) => {
        if (alive) setFridge(items)
      })
      .catch(() => {})
    return () => {
      alive = false
    }
  }, [])

  const activeName = family?.members.find((m) => m.id === family.active_id)?.name ?? null
  return { family, fridge, activeName }
}
