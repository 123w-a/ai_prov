/**
 * 占位房间：户型图里还没装修的房间（第 4 步收藏房已落地，已从这里摘除）。
 *
 * 这里刻意**不摆任何数据**——后端能力一格没接，画个空表或者编两行假内容，
 * 等于告诉用户"这间房有东西"。它只做三件事：说明这间房存在、装什么、怎么回今晚。
 *
 * 文案表放本文件而不放 Shell：Shell 是导航壳，只管"切到哪个房间"，
 * 不该知道每个房间里将要装什么；否则以后加房间要同时改两个文件。
 */

type RoomInfo = {
  title: string
  plan: string[]
  step: string
}

const ROOMS: Record<string, RoomInfo> = {
  weekly: {
    title: '周报',
    step: '落地顺序第 3 步',
    plan: [
      '本周餐次、日期区间、营养灯计数',
      '趋势：后端给“数据不足”就照实写，不画假趋势线',
      '护栏触发次数；点某一顿 → 回今晚并带上那顿的上下文',
    ],
  },
  svc: {
    title: '服务',
    step: '落地顺序第 5 步',
    plan: [
      '服务推荐与预览、来源与适用条件',
      '附近门店、位置解析',
      '无服务时给解释性空态，不出现假数据',
    ],
  },
}

export function RoomPlaceholder({ id, onBack }: { id: string; onBack: () => void }) {
  const info = ROOMS[id]
  if (!info) return null

  return (
    <section className="room-todo" data-room={id}>
      <h2 className="room-todo-h">{info.title} · 还没装修</h2>
      <p className="room-todo-note">
        户型图把它排在{info.step}。这间房现在只有结构，内容一行没写——
        所以这里不摆任何数据，摆出来的都会是假的。
      </p>
      <div className="room-todo-k">装修时要装的东西</div>
      <ul className="room-todo-list">
        {info.plan.map((t) => (
          <li key={t}>{t}</li>
        ))}
      </ul>
      <button className="room-todo-back" type="button" onClick={onBack}>
        回今晚
      </button>
    </section>
  )
}
