import { useEffect, useState, type ReactNode } from 'react'
import { RoomPlaceholder } from '../views/RoomPlaceholder.tsx'

/**
 * 全局导航壳（2026-09-29 户型图第 2 步）。
 *
 * 它只做一件事：让另外三个房间**看得见、点得动、刷新不丢、后退不迷路**。
 * 今晚房内的一切（AskView / WaitCard / ResultView 的三态、成员上下文、会话存档）
 * 全部原样保留在 TonightApp 里，一行没改——这一步是接上导航，不是重建。
 *
 * 为什么用 hash 而不是路由库：
 *   · 刷新不丢房间 → URL 里本来就有 `#room=xxx`，读回来即可；
 *   · 后退不迷路   → 每次切换是一条真实的历史条目，浏览器自己管；
 *   · 不引依赖    → 当前架构刻意没有路由库，要引入得先单独论证成本（户型图红线）。
 * 两条判据都由浏览器保证，而不是我写的代码保证。
 */

const ROOMS = ['tonight', 'weekly', 'fav', 'svc'] as const
type RoomId = (typeof ROOMS)[number]

const ROOM_LABEL: Record<RoomId, string> = {
  tonight: '今晚',
  weekly: '周报',
  fav: '收藏',
  svc: '服务',
}

/** 从 hash 认房间。认不出（空、写错、手改过）一律回今晚——
 *  宁可把用户放回主面，也不要把他放进一个不存在的房间。 */
function readRoom(): RoomId {
  const m = /room=([a-z]+)/.exec(window.location.hash)
  if (m && (ROOMS as readonly string[]).includes(m[1])) return m[1] as RoomId
  return 'tonight'
}

export default function Shell({
  tonight,
  weekly,
  fav,
}: {
  tonight: ReactNode
  weekly: ReactNode
  fav: ReactNode
}) {
  const [room, setRoom] = useState<RoomId>(readRoom)

  useEffect(() => {
    const sync = () => setRoom(readRoom())
    window.addEventListener('hashchange', sync)
    return () => window.removeEventListener('hashchange', sync)
  }, [])

  const go = (id: RoomId) => {
    // 点当前项不写 hash：否则每点一次都多一条历史，后退键会被同一页塞满而失效。
    if (id === readRoom()) return
    // 改 hash 会触发 hashchange → setRoom，页面与 URL 由同一条路径更新，
    // 不在这里另调 setRoom，避免状态与地址栏各走各的。
    window.location.hash = 'room=' + id
  }

  return (
    <div className="shell">
      <nav className="shell-nav" aria-label="主导航">
        <span className="shell-brand">小膳管家</span>
        {ROOMS.map((id) => (
          <button
            key={id}
            type="button"
            data-room={id}
            className={`shell-tab${room === id ? ' is-on' : ''}`}
            aria-current={room === id ? 'page' : undefined}
            onClick={() => go(id)}
          >
            {ROOM_LABEL[id]}
          </button>
        ))}
        {/* 家庭成员切换是全局上下文，不是房间——按户型图先占位，抽屉在后续切片接上。 */}
        <span className="shell-ctx">家庭成员 · 全局上下文</span>
      </nav>

      <div className="shell-main">
        {/*
          今晚房**常驻挂载、只藏不卸**：用户跑到一半切去别的房再回来，
          这一轮的 run、心跳、2 秒存档定时器必须还活着。卸载它会把那一轮
          判成断线——那是替用户编造"他离开过"（同 TonightApp 里取消不伪装
          成失败的理由）。
          其余房间没有跨房状态，按需挂载即可。
        */}
        <div className="shell-room" hidden={room !== 'tonight'}>
          {tonight}
        </div>
        {room === 'weekly' && <div className="shell-room">{weekly}</div>}
        {/* 收藏房同周报房：按需挂载。它唯一的跨房动作（点条目回今晚）走
            自定义事件，不需要自己常驻——常驻的只有今晚房。 */}
        {room === 'fav' && <div className="shell-room">{fav}</div>}
        {room !== 'tonight' && room !== 'weekly' && room !== 'fav' && (
          <div className="shell-room">
            <RoomPlaceholder id={room} onBack={() => go('tonight')} />
          </div>
        )}
      </div>
    </div>
  )
}
