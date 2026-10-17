import { useEffect } from 'react'
import { MemberWeekDetail } from './MemberWeekDetail.tsx'
import type { MemberWeekView } from '../data/firstScreen.ts'

/**
 * 成员详情抽屉的**壳**（2026-10-15）。用户裁决的形式：周报页点某位成员的卡片
 * → 右侧抽屉。
 *
 * ── 为什么是右侧抽屉，而不是独立页面 / 就地展开 / 整页替换 ──────────────
 * · 独立路由：本仓有一条明确红线「**不引入路由库**」（app/Shell.tsx 文件头写了，
 *   要引入得先单独论证成本）。为一张详情页破例，代价远大于收益。
 * · 就地展开：详情量比"一道菜的几顿"大得多（达标 + 趋势 + 菜 + 逐餐 + 建议），
 *   展开会把左列拉得比右列长很多——同一条理由已写在 WeeklyView 的 openDish 注释里。
 * · 整页替换：家庭总览被盖掉，用户失去"这位成员在家庭里的位置"这个参照，
 *   而且它把家庭与成员做成了两个层级——与「同级对象」的裁决相冲。
 * 右侧抽屉让周报页在背后保持可见，面板自己标明是**家庭报告里的成员详情**，层级没变。
 *
 * ── 两条状态纪律 ────────────────────────────────────────────────────────
 * 1 抽屉状态**不进 hash**（Shell.tsx 的明确要求）：hash 是房间导航，把面板塞进去
 *   会让"返回"在用户没预期的时候关掉抽屉、且刷新后凭空弹出来。
 * 2 空态分两种，**不合并**：`view.isEmpty` 是"这位成员这周真没记到"（内容性空，
 *   写后端给的原因）；`error` 是"接口读失败"（故障，必须给得出原因，
 *   不能让用户以为这周没吃饭）。两者在页面上长得一样就是本仓反复点名的老坑。
 *
 * 壳与内容分两个文件是因为 blocks/ 有 150 行上限（check-layers 的规模上限）：
 * 这里只留遮罩 + 页头 + Esc 与三种状态的分发，具体内容在 MemberWeekDetail。
 */
export function MemberWeekDrawer({
  name,
  view,
  loading,
  error,
  onClose,
}: {
  name: string
  view: MemberWeekView | null
  loading: boolean
  error: string | null
  onClose: () => void
}) {
  /* Esc 关掉。与 Shell 的成员抽屉同一套三向关闭（遮罩 / Esc / 按钮）——
     面板是个 aria-modal 对话框，键盘用户没有 Esc 就只能靠 Tab 摸到关闭键。 */
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  return (
    <>
      {/* 遮罩：点它关闭。aria-hidden —— 它没有语义，只是"点这里退出"的一层。 */}
      <div className="wk-mask" onClick={onClose} aria-hidden="true" />
      <aside className="wk-drawer" role="dialog" aria-modal="true" aria-label={`${name}的这一周`}>
        <header className="wk-dr-head">
          <div>
            <h3 className="wk-dr-name">{name}</h3>
            <p className="wk-dr-sub">近 7 天 · 只算这位参与的餐次</p>
          </div>
          <button className="wk-dr-close" type="button" onClick={onClose} aria-label="关闭详情">
            ×
          </button>
        </header>

        <div className="wk-dr-body">
          {loading && <p className="wk-dr-note">正在读取 {name} 的这一周…</p>}
          {!loading && error && <p className="wk-dr-err">读取失败：{error}</p>}
          {!loading && !error && view && view.isEmpty && <p className="wk-dr-note">{view.message}</p>}
          {!loading && !error && view && !view.isEmpty && (
            <MemberWeekDetail name={name} view={view} />
          )}
        </div>
      </aside>
    </>
  )
}
