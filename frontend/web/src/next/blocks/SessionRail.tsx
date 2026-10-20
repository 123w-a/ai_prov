import { useLayoutEffect, useState } from 'react'
import { createPortal } from 'react-dom'
import type { RailSession } from '../data/railTime.ts'
import { lastMs, titleOf } from '../data/railTime.ts'
import { SessionRow } from './SessionRow.tsx'

/**
 * 历史会话常驻栏（2026-10-19 重写：左浮层 → 左栏常驻块）。
 *
 * 归属：只在今晚房出现、只读 fetchSessions()；标题、排序、空会话分组沿用
 * data/railTime.ts 的同一份纯函数（e 门禁：blocks 不引后端类型）。
 *
 * 与旧浮层版的三点不同（实施单第 4 条）：
 *   1. 用 createPortal 定位到 Shell 竖栏的 #session-rail-slot（家庭成员区与
 *      轨底藤蔓之间）——它是竖栏的一块，不再是盖在页面上的浮层；
 *   2. 移除 open / 遮罩 / 关闭：常驻可见，没有「打开」这个状态；
 *   3. 新增「新建会话」与创建中状态。
 * 删除 / 改名从旧前端 SessionSidebar 找回（2026-10-19）：行内交互在 SessionRow，
 * 确认框与善后（删到只剩自己时新建）在 app 层。窄屏由 session.css 收成
 * 主导航里的紧凑横向区块，不做不可达浮层。
 *
 * slot 找不到（夹具台没挂 Shell）就整块不画——画在错的地方比不画更糟。
 */

/** 默认铺开的会话条数。实测本机 318 个会话，全量铺进 240px 的滚动区等于不可用。 */
const RAIL_LIMIT = 14

export function SessionRail({
  sessions,
  activeId,
  notice,
  loading,
  error,
  creating = false,
  onPick,
  onNewSession,
  onRename,
  onDelete,
}: {
  sessions: RailSession[]
  activeId: string | null
  notice: string | null
  loading: boolean
  error: string | null
  /** 新建会话进行中：按钮禁用并显示「创建中…」。 */
  creating?: boolean
  onPick: (sid: string) => void
  onNewSession: () => void
  /** 行内改名提交（Enter/失焦）。空名已在 SessionRow 挡掉，到不了这里。 */
  onRename: (sid: string, title: string) => void
  /** 删除意图。确认框与善后逻辑在 app 层（删当前会话要落位到剩余第一个或新建）。 */
  onDelete: (sid: string) => void
}) {
  const [slot, setSlot] = useState<HTMLElement | null>(null)
  // 搜索与「显示全部」的状态必须声明在下面 `if (!slot) return null` **之前**：
  // slot 首帧是 null（会在渲染后才有值），写在提前返回之后会让首帧少调两个
  // hook、次帧多调两个，React 直接报 "change in the order of Hooks"。
  const [q, setQ] = useState('')
  const [all, setAll] = useState(false)
  // useLayoutEffect 而不是 useEffect：挂载当帧就位，不闪一帧空槽。
  useLayoutEffect(() => {
    setSlot(document.getElementById('session-rail-slot'))
  }, [])
  if (!slot) return null

  // 不依赖后端返回顺序：按最后活跃时间自己倒序。
  // 2026-10-19 用户裁决：「空会话」分组是额外的杂物，撤掉——所有会话一张列表。
  // 空会话与有内容的混排（它们本来也是会话，只是还没说话），清杂物靠行内「删除」。
  const byLast = (a: RailSession, b: RailSession) => lastMs(b) - lastMs(a)
  const sorted = [...sessions].sort(byLast)

  // 实测这台机器上有 318 个会话。原来它们全部塞进一个 240px 的滚动区——
  // 滚动条只有两行高，等于不可用。现在默认只铺最近 14 个，其余靠搜索或「显示全部」。
  const kw = q.trim()
  const filtered = kw ? sorted.filter((s) => titleOf(s).includes(kw)) : sorted
  const shown = all || kw ? filtered : filtered.slice(0, RAIL_LIMIT)
  const rest = filtered.length - shown.length

  return createPortal(
    <div className="sr">
      <div className="sr-head">
        <h2 className="sr-h">历史会话</h2>
        <button type="button" className="sr-new" disabled={creating} onClick={onNewSession}>
          {creating ? '创建中…' : '＋ 做一顿新的'}
        </button>
      </div>
      <p className="sr-sub">点一条进去看全部问答。改名 / 删除都在行内。</p>

      {sorted.length > RAIL_LIMIT && (
        <input
          className="sr-search"
          type="search"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder={`搜索 ${sorted.length} 个会话…`}
          aria-label="搜索历史会话"
        />
      )}

      {notice && (
        <p className="sr-notice" role="status">
          {notice}
        </p>
      )}
      {loading && <p className="sr-note">正在读会话列表…</p>}
      {error && (
        <p className="sr-err" role="alert">
          {error}
        </p>
      )}
      {!loading && !error && sessions.length === 0 && <p className="sr-note">还没有任何会话。</p>}
      {kw !== '' && filtered.length === 0 && <p className="sr-note">没有标题含「{kw}」的会话。</p>}

      {shown.length > 0 && (
        <ul className="sr-list">
          {shown.map((s) => (
            <SessionRow
              key={s.session_id}
              s={s}
              active={s.session_id === activeId}
              onPick={onPick}
              onRename={onRename}
              onDelete={onDelete}
            />
          ))}
        </ul>
      )}

      {kw === '' && rest > 0 && (
        <button type="button" className="sr-more" onClick={() => setAll(true)}>
          还有 {rest} 个会话 · 显示全部
        </button>
      )}
      {kw === '' && all && filtered.length > RAIL_LIMIT && (
        <button type="button" className="sr-more" onClick={() => setAll(false)}>
          收起到最近 {RAIL_LIMIT} 个
        </button>
      )}
    </div>,
    slot,
  )
}
