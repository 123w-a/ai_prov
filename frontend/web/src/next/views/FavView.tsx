import { useEffect, useRef, useState } from 'react'
import {
  forgetFav,
  rateFav,
  restarFav,
  unstarFav,
  useFavorites,
  type FavEntry,
  type FavRating,
} from '../data/favorites.ts'

/**
 * 收藏房间（2026-09-29 户型图第 4 步 · 落地顺序第 4 间）。
 *
 * 四条计划（RoomPlaceholder 里那三条 + 判据）逐条落地：
 *   · 收藏列表   → GET /api/favorites，加载 / 空 / 失败三态分开画；
 *   · 取消收藏   → 乐观摘掉 + 「撤销」窗口（旧盘点裁决：必须可撤销或二次确认，
 *                  且失败不能只默默回滚——失败时 reload 回滚并显示错误）；
 *   · 点赞点踩   → 后端同值再点=取消，页面显示**后端返回的终态**；初始态另拉
 *                  会话记录建立，读不到就写「状态未加载」（不把未知画成没点过）；
 *   · 忘掉这道菜 → removed=0 时说「没有踩记录，偏好没改变」，不冒充成功删了东西；
 *   · 点条目回今晚 → 自定义事件 fav-open（与周报房 dish-pick 同构，走常驻挂载的
 *                  今晚房监听）：有 answer 恢复成只读结果详情，没有就把原话预填。
 *
 * 空态文案刻意不写「在今晚房点 ★」：新前端的结果页**还没有收藏按钮**，
 * 那是后续切片的事。写成有，就是教用户去点一个不存在的东西。
 */
type Notice = { text: string; tone: 'ok' | 'warn'; undo?: FavEntry }

const errText = (e: unknown) => (e instanceof Error ? e.message : String(e))

export default function FavView() {
  const { entries, error, ratings, ratingsReady, reload, drop, setRating } = useFavorites()
  const [notice, setNotice] = useState<Notice | null>(null)
  const [busy, setBusy] = useState(false)
  const timer = useRef<number | undefined>(undefined)

  useEffect(() => () => window.clearTimeout(timer.current), [])

  const say = (text: string, tone: Notice['tone'], undo?: FavEntry) => {
    window.clearTimeout(timer.current)
    setNotice({ text, tone, undo })
    timer.current = window.setTimeout(() => setNotice(null), 10000)
  }

  const open = (e: FavEntry) => {
    window.dispatchEvent(
      new CustomEvent('fav-open', { detail: { ask: e.ask, dish: e.dish, detail: e.detail } }),
    )
    window.location.hash = 'room=tonight'
  }

  const remove = async (e: FavEntry) => {
    if (busy) return
    setBusy(true)
    drop(e.key)
    try {
      await unstarFav(e)
      say(`已取消收藏「${e.dish}」`, 'ok', e)
    } catch (err) {
      say(`取消收藏失败：${errText(err)}`, 'warn')
      reload() // 服务端没改成功 ⇒ 以它为准把条目放回来
    } finally {
      setBusy(false)
    }
  }

  const undoRemove = async (e: FavEntry) => {
    if (busy) return
    setBusy(true)
    setNotice(null)
    try {
      await restarFav(e)
      reload()
    } catch (err) {
      say(`恢复收藏失败：${errText(err)}`, 'warn')
      reload()
    } finally {
      setBusy(false)
    }
  }

  const rate = async (e: FavEntry, r: FavRating) => {
    if (busy) return
    setBusy(true)
    try {
      const final = await rateFav(e, r)
      setRating(e.key, final)
      say(
        final === null
          ? `已取消对「${e.dish}」的反馈`
          : final === 'up'
            ? `已记为赞：${e.dish}`
            : `已记为踩：${e.dish}`,
        'ok',
      )
    } catch (err) {
      say(`反馈没有记上：${errText(err)}`, 'warn')
    } finally {
      setBusy(false)
    }
  }

  const forget = async (e: FavEntry) => {
    if (busy) return
    setBusy(true)
    try {
      const r = await forgetFav(e)
      say(
        r.removed > 0
          ? `已忘掉「${r.dish}」：清掉 ${r.removed} 条踩记录，之后不会再避开它`
          : `没有找到「${e.dish}」的踩记录，偏好没有改变`,
        r.removed > 0 ? 'ok' : 'warn',
      )
    } catch (err) {
      say(`忘掉这道菜失败：${errText(err)}`, 'warn')
    } finally {
      setBusy(false)
    }
  }

  /* 回执条（含「撤销」）必须在**每个分支**都渲染，包括空态：
     刚把最后一条取消掉时列表正好变空，若空态分支把它吞掉，
     「取消收藏可撤销」的承诺就只在还剩 ≥2 条时才成立——那等于没承诺。 */
  const noticeEl = notice ? (
    <p className="fv-note" data-tone={notice.tone} role="status">
      {notice.text}
      {notice.undo && (
        <>
          {' '}
          <button
            className="fv-undo"
            type="button"
            disabled={busy}
            onClick={() => void undoRemove(notice.undo as FavEntry)}
          >
            撤销
          </button>
        </>
      )}
    </p>
  ) : null

  /* ── 三个加载态：失败 / 在读 / 真的空，各画各的 ──────────────────────── */

  if (error) {
    return (
      <section className="fv" data-room="fav">
        <h2 className="fv-h">收藏</h2>
        {noticeEl}
        <p className="fv-err">收藏读取失败：{error}</p>
        <button className="fv-retry" type="button" onClick={reload}>
          重试
        </button>
      </section>
    )
  }

  if (entries === null) {
    return (
      <section className="fv" data-room="fav">
        <h2 className="fv-h">收藏</h2>
        {noticeEl}
        <p className="fv-loading">正在读取收藏…</p>
      </section>
    )
  }

  if (entries.length === 0) {
    return (
      <section className="fv" data-room="fav">
        <h2 className="fv-h">收藏</h2>
        {noticeEl}
        <p className="fv-empty">
          还没有收藏。目前只有旧版首页的回答旁有 ★ 可以收藏；
          新前端结果页的收藏按钮还没做，做之前这里会一直空着。
        </p>
      </section>
    )
  }

  return (
    <section className="fv" data-room="fav">
      <h2 className="fv-h">收藏</h2>
      <p className="fv-sub">{entries.length} 条 · 点一条回今晚看那顿的详情</p>

      {noticeEl}

      {!ratingsReady && (
        <p className="fv-hint">
          赞/踩的既有状态没能读出来，这里先不显示（不猜）。
          点一下会按后端返回的真实结果更新。
          <button className="fv-retry" type="button" onClick={reload}>
            重试
          </button>
        </p>
      )}

      <ul className="fv-list">
        {entries.map((e) => (
          <li className="fv-item" key={e.key}>
            <button className="fv-open" type="button" onClick={() => open(e)}>
              {e.imageUrl && (
                <img className="fv-thumb" src={e.imageUrl} alt="" loading="lazy" />
              )}
              <span className="fv-main">
                <span className="fv-dish">{e.dish}</span>
                <span className="fv-from">来自「{e.from}」</span>
                {e.ask && <span className="fv-ask">{e.ask}</span>}
              </span>
              <span className="fv-go">
                {e.detail ? '看这顿的结果 →' : '带着原话回今晚 →'}
              </span>
            </button>
            <div className="fv-acts">
              <button
                className="fv-btn"
                type="button"
                data-on={ratings[e.key] === 'up' || undefined}
                disabled={busy}
                onClick={() => void rate(e, 'up')}
              >
                赞
              </button>
              <button
                className="fv-btn"
                type="button"
                data-on={ratings[e.key] === 'down' || undefined}
                disabled={busy}
                onClick={() => void rate(e, 'down')}
              >
                踩
              </button>
              <button
                className="fv-btn"
                type="button"
                disabled={busy}
                onClick={() => void forget(e)}
              >
                忘掉这道菜
              </button>
              <button
                className="fv-btn fv-btn-danger"
                type="button"
                disabled={busy}
                onClick={() => void remove(e)}
              >
                取消收藏
              </button>
            </div>
          </li>
        ))}
      </ul>
    </section>
  )
}
