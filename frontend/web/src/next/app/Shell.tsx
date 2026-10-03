import { useEffect, useState, type ReactNode } from 'react'
import { switchActiveMember } from '../../api/client.ts'
import { familyFacts, notifyMemberSwitched, useHousehold } from '../data/household.ts'

/**
 * 全局导航壳（2026-09-29 户型图第 2 步）。
 *
 * 它只做一件事：让另外三个房间**看得见、点得动、刷新不丢、后退不迷路**。
 * 今晚房内的一切（AskView / WaitCard / ResultView 的三态、会话存档）
 * 全部原样保留在 TonightApp 里——这一步是接上导航，不是重建。
 *
 * 为什么用 hash 而不是路由库：
 *   · 刷新不丢房间 → URL 里本来就有 `#room=xxx`，读回来即可；
 *   · 后退不迷路   → 每次切换是一条真实的历史条目，浏览器自己管；
 *   · 不引依赖    → 当前架构刻意没有路由库，要引入得先单独论证成本（户型图红线）。
 * 两条判据都由浏览器保证，而不是我写的代码保证。
 *
 * 家庭成员抽屉（2026-10-01 B 批次①，lave gpt-5.6-sol 方案 B）：
 *   · 全局上下文不是房间 ⇒ 挂在本组件、由 React state 控制，
 *     **hash 不承载抽屉状态**（打开抽屉不该多一条历史、不该让后退键迷路）；
 *   · 任意房可开、Escape / 遮罩 / 关闭键三路关闭；
 *   · 成员切换走真端点 switchActiveMember（后端 active_id 是权威，刷新后仍保持），
 *     成功才广播 notifyMemberSwitched —— 起手页的「按谁推荐」会跟着刷新，
 *     而正在等待中的这一轮是 live:false 快照，不改口（绑定启动时的档案）。
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

const errText = (e: unknown): string => (e instanceof Error ? e.message : String(e))

export default function Shell({
  tonight,
  weekly,
  fav,
  svc,
}: {
  tonight: ReactNode
  weekly: ReactNode
  fav: ReactNode
  svc: ReactNode
}) {
  const [room, setRoom] = useState<RoomId>(readRoom)
  const [drawer, setDrawer] = useState(false)
  const [switching, setSwitching] = useState<string | null>(null)
  const [switchErr, setSwitchErr] = useState<string | null>(null)

  const { family, activeName } = useHousehold()
  const facts = familyFacts(family)

  useEffect(() => {
    const sync = () => setRoom(readRoom())
    window.addEventListener('hashchange', sync)
    return () => window.removeEventListener('hashchange', sync)
  }, [])

  // Escape 关闭（只在抽屉打开时挂监听，不打扰正常键入）。
  useEffect(() => {
    if (!drawer) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setDrawer(false)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [drawer])

  const go = (id: RoomId) => {
    // 点当前项不写 hash：否则每点一次都多一条历史，后退键会被同一页塞满而失效。
    if (id === readRoom()) return
    // 改 hash 会触发 hashchange → setRoom，页面与 URL 由同一条路径更新，
    // 不在这里另调 setRoom，避免状态与地址栏各走各的。
    window.location.hash = 'room=' + id
  }

  const pick = async (id: string) => {
    if (!family || family.active_id === id || switching) return
    setSwitching(id)
    setSwitchErr(null)
    try {
      await switchActiveMember(id)
      // 以后端返回为准：成功才广播，起手页等 live 订阅者重读到新档案。
      notifyMemberSwitched()
      setDrawer(false)
    } catch (err) {
      setSwitchErr(`切换没有成功（当前推荐没有变）：${errText(err)}`)
    } finally {
      setSwitching(null)
    }
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
        {/* 家庭成员切换是全局上下文，不是房间（户型图裁决）：按钮显示当前推荐，
            点开抽屉。hash 不承载抽屉状态。 */}
        <span className="shell-ctx">
          <button type="button" className="shell-ctx-btn" onClick={() => setDrawer(true)}>
            {activeName ? `家庭成员 · ${activeName}` : '家庭成员'}
          </button>
        </span>
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
        {/* 服务房（第 5 步）：四个房都在下面四条分支里渲染完毕，
            RoomPlaceholder 占位组件已随之完成使命并删除。 */}
        {room === 'svc' && <div className="shell-room">{svc}</div>}
      </div>

      {/* 家庭成员抽屉：遮罩点击与 Escape 都关；面板内点击 stopPropagation
          不穿透到底层房间（否则关不掉还会误点导航）。 */}
      {drawer && (
        <div className="sd-mask" onClick={() => setDrawer(false)}>
          <aside
            className="sd"
            role="dialog"
            aria-modal="true"
            aria-label="家庭成员"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="sd-head">
              <h2 className="sd-h">家庭成员</h2>
              <button type="button" className="sd-close" onClick={() => setDrawer(false)}>
                关闭
              </button>
            </div>
            <p className="sd-sub">谁在被推荐 · 切换后下一条消息生效</p>

            {family && family.members.length > 0 ? (
              <ul className="sd-list">
                {family.members.map((m, i) => {
                  const on = family.active_id === m.id
                  // 标签复用 familyFacts 的同一份推导（推导只留一份，不许在这里抄一遍）。
                  // 但 facts 行以「名字 · 标签」开头，旁边已有 sd-name——直接放会显示成
                  // 「小美 小美 · 孕妇」，所以去掉名字前缀只留标签；没标签就不占位。
                  const full = facts?.members[i] ?? m.name
                  const prefix = `${m.name} · `
                  const label = full.startsWith(prefix) ? full.slice(prefix.length) : ''
                  return (
                    <li key={m.id}>
                      <button
                        type="button"
                        className="sd-item"
                        data-on={on}
                        disabled={on || switching !== null}
                        onClick={() => void pick(m.id)}
                      >
                        <span className="sd-name">{m.name}</span>
                        {label && <span className="sd-desc">{label}</span>}
                        {on && <span className="sd-on">当前推荐</span>}
                        {switching === m.id && <span className="sd-busy">切换中…</span>}
                      </button>
                    </li>
                  )
                })}
              </ul>
            ) : (
              <p className="sd-empty">档案还没有读出来，先不猜成员。</p>
            )}

            {facts?.shared && <p className="sd-shared">{facts.shared}</p>}
            {switchErr && (
              <p className="sd-err" role="alert">
                {switchErr}
              </p>
            )}
            <p className="sd-note">
              切换只影响下一条消息。正在等待中的这一轮仍按启动时的档案跑完，不会中途换人。
            </p>
          </aside>
        </div>
      )}
    </div>
  )
}
