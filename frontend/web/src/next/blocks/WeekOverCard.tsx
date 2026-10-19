/**
 * 卡 7「本周 N 处超标」：只列**真的判了红灯**的顿（设计稿第二张卡）。
 *
 * 为什么不把黄灯也收进来：黄的意思是"接近上限"，把它和超标并列会让用户以为
 * 黄也得改；而这张卡要回答的是"哪一顿已经过线了"。周报的篇幅该留给能动手的事。
 *
 * 为什么没有「钠 2.8 g，超出单餐上限 1.6 g」这种数字：`meals.json` 里没有份量、
 * 没有克数（本周 169 条记录 ingredients 0 条、amount_g 0 个）。写不出克数就
 * 不写——编一个"2.8 g"比不写更糟，那是给用户一个可被复算打脸的假精确。
 * 能确凿说的只有：哪一顿、哪道菜、哪一项被判红了。
 *
 * `advice` 由后端按**营养素**给一句通用做法，不是针对这道菜的定制建议
 * （记账行里没存做法，说不出"把豆瓣酱减半"这种话）。
 */
import { CardHead } from './WeekCardShell.tsx'
import type { OverItem } from '../data/firstScreen.ts'

export function WeekOverCard({ items }: { items: OverItem[] }) {
  if (items.length === 0) return null
  return (
    <section className="wk-card is-over">
      {/* 「N 顿」而不是「N 处」：一条 = 一顿（早/午/晚里的一格），
          一顿里可能同时有两道菜、两项营养素超了。"处"会把两者混在一起数。 */}
      <CardHead n="7" title={`本周 ${items.length} 顿超标`} />
      {/* 补一句单位说明（2026-10-19）：护栏提示卡里写的是「N 道菜超标」，
          这里写的是「N 顿超标」，两个数对不上时读者会以为数据错了。
          点明"按整餐算"之后，3 道菜 / 2 顿就是同一件事的两种数法。 */}
      <p className="wk-card-sub">按整餐算：一顿里有一项超标就记作这顿超标</p>
      {items.map((o) => {
        const dishes = new Set(o.bad.map((b) => b.dish))
        const first = o.bad[0]?.dish || o.dishes[0] || '这道菜'
        const dishLabel = dishes.size > 1 ? `${first} 等 ${dishes.size} 道` : first
        return (
          <div className="wk-over-item" key={`${o.date}-${o.slot}`}>
            <p className="wk-over-h">
              <b>
                周{o.weekday}
                {o.slot}餐
              </b>
              <span>· {dishLabel}</span>
            </p>
            {/* 只写"哪一项超了"，不写克数：记账行里没有份量（本周 169 条
                ingredients 0 条、amount_g 0 个），编一个「钠 2.8 g」经不起复算。 */}
            <p className="wk-over-v">{o.nutrients.join('、')} 超标</p>
            {o.advice ? <p className="wk-over-t">{o.advice}</p> : null}
          </div>
        )
      })}
    </section>
  )
}
