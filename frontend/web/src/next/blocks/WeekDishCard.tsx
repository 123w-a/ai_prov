/**
 * C 设计稿的卡 3「常做的菜 Top 5」。
 *
 * 从 `WeeklyView` 整段搬进来（连同 `openDish` 展开态），原因是周报页正按设计稿
 * 重排成编号卡片；菜这一段自带展开明细的状态与交互，留在视图里会让 `WeeklyView`
 * 一边排卡片一边管一道菜的开合。展开状态跟着组件走，也顺带把 `openDish` 从视图
 * 的顶层 state 里摘掉了。
 *
 * 两条保留的既有纪律（都是之前踩坑定的，别在搬家时丢掉）：
 *   · **有图渲染图、没图同尺寸占位**：几何稳定，也不去猜一个不存在的图片地址。
 *   · **每一顿少于 1 条时不给展开符**：点了没反应比没有更糟。
 */
import { useState } from 'react'
import { CardHead } from './WeekCardShell'
import { mealLevel, mealStamp } from '../data/weekFacts.ts'

type MealDetail = {
  ts: string
  dish: string
  member?: string | null
  lights: Array<{ nutrient: string; level: string }>
  onTarget: boolean
}

export function WeekDishCard({
  dishes,
  dishImages,
  mealsDetail,
  onPick,
}: {
  dishes: Array<[string, number]>
  dishImages: Record<string, string>
  mealsDetail: MealDetail[]
  onPick: (name: string) => void
}) {
  const [openDish, setOpenDish] = useState<string | null>(null)
  if (dishes.length === 0) return null
  return (
    <section className="wk-card is-dishes">
      <CardHead n="4" title="常做的菜 Top 5" note="· 点一道回今天" />
      <ul className="wk-dishes">
        {dishes.map(([name, n]) => {
          const img = dishImages[name]
          const rows = mealsDetail.filter((m) => m.dish === name)
          const open = openDish === name
          return (
            <li key={name}>
              <div className="wk-dish-row">
                {/* title 给全名：窄列里菜名会被截断（实测 85px，「西兰花炒蛋拌面
                    （增肌少油版）」这类长名会截成三个一模一样的「西兰花炒…」，
                    光看列表分不出是哪道菜）。截断本身是对的——它保证每行等高，
                    但没有 title 的话信息就真丢了。 */}
                <button className="wk-dish" type="button" title={name} onClick={() => onPick(name)}>
                  {img ? (
                    <img className="wk-dish-img" src={img} alt="" loading="lazy" />
                  ) : (
                    <span className="wk-dish-img is-empty" aria-hidden="true" />
                  )}
                  <span className="wk-dish-name">{name}</span>
                  <b className="wk-dish-n">{n}</b>
                  <span className="wk-dish-go">再做一顿 →</span>
                </button>
                {rows.length > 0 ? (
                  <button
                    className="wk-dish-more"
                    type="button"
                    aria-expanded={open}
                    aria-label={`${open ? '收起' : '展开'}「${name}」的每一顿`}
                    onClick={() => setOpenDish(open ? null : name)}
                  >
                    {open ? '▲' : '▼'}
                  </button>
                ) : null}
              </div>
              {open ? (
                <ul className="wk-meals">
                  {rows.map((m) => (
                    <li className="wk-meal" key={m.ts}>
                      <span className="wk-meal-ts">{mealStamp(m.ts)}</span>
                      <span className="wk-meal-lights">
                        {m.lights.map((l) => {
                          const lv = mealLevel(l.level)
                          return (
                            <span className="wk-meal-light" key={l.nutrient}>
                              {l.nutrient}
                              <i className="wk-pip" data-c={lv.c} />
                              {lv.label}
                            </span>
                          )
                        })}
                      </span>
                      <span className="wk-meal-flag">{m.onTarget ? '达标' : '未达标'}</span>
                      {/* 那一顿没记到人就不写（member=null 是老记录），
                          不拿「全家」顶上——那是编的。 */}
                      {m.member ? <span className="wk-meal-who">{m.member}</span> : null}
                    </li>
                  ))}
                </ul>
              ) : null}
            </li>
          )
        })}
      </ul>
    </section>
  )
}



