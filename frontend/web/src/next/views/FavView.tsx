import { useEffect, useRef, useState } from 'react'
import { favoritedAtLabel, progressLine, progressShort } from '../data/fieldFacts.ts'
import { familyRows, type FamilyMemberRow } from '../data/firstScreen.ts'
import { useHousehold } from '../data/household.ts'
import {
  forgetFav,
  rateFav,
  restarFav,
  tagsOf,
  unstarFav,
  useFavorites,
  type FavEntry,
  type FavRating,
} from '../data/favorites.ts'

/**
 * 收藏房间（2026-09-29 户型图第 4 步 · 落地顺序第 4 间）。
 *
 * 2026-10-11 第二轮照稿重建：视觉唯一真值 `.tmp-img/design-round/fav-expect-v4.png`
 * （1536×1024，S = 1366/1536 = 0.8893229166666666）。本轮把页头（大号砖红星形
 * + 副标题小图标 + 页底细横线与中央花章）、卡片（内嵌 4:3 配图 + 菜名 + 三枚
 * 标签胶囊 + 发丝线 + 书签/收藏时间/来源）与右栏三块全部按稿的**实测几何**重排；
 * 所有数值来自 assets 工具的墨迹读数（bandprobe / rowprobe / scanline / 逐倍放大），
 * 不目测。
 *
 * 三条数据纪律（用户口径，不能破）：
 *   1. 真实收藏**只有 1 条**——不复制成 4 张、不编菜名；
 *   2. ~~后端**没有收藏动作时间**字段~~ → ✅ 2026-10-11 起后端有 `favorited_at`
 *      （`storage/sessions.py` 的 `list_starred`），本页已接线；
 *      旧记录没有这个值时才写「待补」，绝不拿这一轮时间顶。
 *   3. ~~成员行的**进度条**后端无字段~~ → ✅ **2026-10-14 更正**：`nutrition_progress`
 *      后端一直有（`reports_route.py` `member_nutrition_progress`），此前只是
 *      `familyRows` 没把它带出来。当时把「这一屏还没接」写成了「后端没有」——
 *      前者是没做完，后者是编造，两件事。槽仍按稿不画填充，但那一行如实给真值。
 *
 * 三条功能纪律（沿用第一轮，不能丢）：
 *   · 取消收藏 → 乐观摘掉 + 「撤销」窗口，失败以服务端为准回滚并报错；
 *   · 点赞点踩 → 显示**后端返回的终态**，初始态另拉会话记录建立，读不到就写
 *     「状态未加载」（不把未知画成没点过）；
 *   · 忘掉这道菜 → removed=0 时说「没有踩记录，偏好没改变」，不冒充成功。
 */
type Notice = { text: string; tone: 'ok' | 'warn'; undo?: FavEntry }

const errText = (e: unknown) => (e instanceof Error ? e.message : String(e))

// 「收藏于」的占位字面量（原 SAVED_AT_PLACEHOLDER）2026-10-11 起只留在 data/fieldFacts.ts
// 一处（FIELD_TODO，随 favoritedAtLabel 一起给出）：后端现在真的会返回收藏时间，
// "什么时候写占位"变成了一条判定，两处各写一份字面量迟早只说对一处。

export default function FavView() {
  const { entries, error, ratings, ratingsReady, reload, drop, setRating } = useFavorites()
  const { family, fridge } = useHousehold()
  const rows = familyRows(family)
  const [notice, setNotice] = useState<Notice | null>(null)
  const [busy, setBusy] = useState(false)
  /** 搜索词与来源筛选（照稿的顶部工具行）。两者都只筛**已有的真数据**：
      搜索匹配菜名与原话，来源胶囊就是 e.from 的实际取值集合——不造分类标签。
      稿上的第二排胶囊（早餐/午餐/晚餐/汤羹/小食）不照搬：那是后端没有的分类字段。 */
  const [q, setQ] = useState('')
  const [src, setSrc] = useState<string | null>(null)
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

  /* 页头（星形 + 收藏 + 副标题 + 下方细横线与中央花章）在四个分支里都要出现：
     它是门牌，不是列表的装饰。稿实测：星形 30.2×29.4 CSS 落在 y92.5、
     标题左缘 318.4、副标题小图标 25.8×23 落在 y138.8。 */
  const headEl = (
    <div className="fv-head">
      <h2 className="fv-h">
        收藏
      </h2>
      <p className="fv-sub">
        <span className="fv-sub-ico" aria-hidden="true" />
        点一条回今天看那顿的详情
      </p>
    </div>
  )

  /* ── 三个加载态：失败 / 在读 / 真的空，各画各的 ──────────────────────── */

  if (error) {
    return (
      <section className="fv" data-room="fav">
        {headEl}
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
        {headEl}
        {noticeEl}
        <p className="fv-loading">正在读取收藏…</p>
      </section>
    )
  }

  if (entries.length === 0) {
    return (
      <section className="fv" data-room="fav">
        {headEl}
        {noticeEl}
        {/* 空态文案改（2026-10-09）：原句"结果页的收藏按钮还没做，做之前这里
            会一直空着"——该判断已过期，结果页动作条的 ★ 现在就在（.rs-acts），
            指路句随之改指新入口。 */}
        <p className="fv-empty">
          还没有收藏。在结果页点 ★ 收藏，想再做的那顿就存到这里；
          回来点一条，直接落回今天看那顿的详情。
        </p>
      </section>
    )
  }

  const sources = [...new Set(entries.map((e) => e.from))].filter((s) => s && s.trim() !== '')
  const needle = q.trim().toLowerCase()
  const shown = entries.filter((e) => {
    if (src !== null && e.from !== src) return false
    if (needle === '') return true
    return `${e.dish} ${e.ask ?? ''}`.toLowerCase().includes(needle)
  })

  return (
    <section className="fv" data-room="fav">
      <div className="fv-body">
        {headEl}

        {/* 工具行（照稿）：左边来源胶囊、右边搜索框。
            稿上的胶囊是「全部 早餐 午餐 晚餐 汤羹 小食」六枚——餐次/汤羹/小食
            后端**没有**这个分类字段，照搬就是编分类；这里只放真实来源的胶囊。 */}
        <div className="fv-tools">
          <div className="fv-pills" role="group" aria-label="按来源筛选">
            <button
              type="button"
              className="fv-pill"
              data-on={src === null || undefined}
              onClick={() => setSrc(null)}
            >
              全部
            </button>
            {sources.map((s) => (
              <button
                key={s}
                type="button"
                className="fv-pill"
                data-on={src === s || undefined}
                onClick={() => setSrc(src === s ? null : s)}
              >
                {s}
              </button>
            ))}
          </div>
          <div className="fv-search-wrap">
            <span className="fv-search-ico" aria-hidden="true" />
            <input
              className="fv-search"
              type="search"
              value={q}
              placeholder="搜索收藏的菜品…"
              onChange={(ev) => setQ(ev.target.value)}
            />
          </div>
        </div>

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

        {shown.length === 0 && (
          <p className="fv-hint">没有匹配的收藏，换个词或点「全部」。</p>
        )}

        <ul className="fv-list">
          {shown.map((e) => {
            // 一行胶囊：餐次 + 营养灯，都是真值（见 favorites.ts tagsOf 的推导纪律）。
            const tags = tagsOf(e)
            return (
              <li className="fv-item" key={e.key}>
                <button className="fv-open" type="button" onClick={() => open(e)}>
                  {e.imageUrl && (
                    <img className="fv-thumb" src={e.imageUrl} alt="" loading="lazy" />
                  )}
                  {/* 稿上从配图往下依次是：菜名 → 标签胶囊行 → 发丝线 → 元信息。
                      所以 .fv-main 只装「菜名 + 标签」，时间与来源另起 .fv-meta。 */}
                  <span className="fv-main">
                    <span className="fv-dish">{e.dish}</span>
                    {tags.length > 0 && (
                      <span className="fv-tags">
                        {tags.map((t, i) => (
                          <span
                            className="fv-tag"
                            data-level={t.level ?? undefined}
                            key={`${t.level ?? 'meal'}:${t.text}:${i}`}
                          >
                            {t.text}
                          </span>
                        ))}
                      </span>
                    )}
                  </span>
                  {/* 稿上的元信息是「🖿 收藏于 10-28 / 来自：小膳管家」。
                      「收藏于」的值取后端收藏动作时间（favorited_at，2026-10-11 新增）；
                      老收藏没有这个值就写「待补」（见 savedAtOf）。下一行的
                      「这顿 · MM-DD」是真实的一轮提问时间（FavEntry.turnedAt，
                      见 favorites.ts 的注释），两者不是一回事，所以都保留并标出含义；
                      「来自：X」按稿用真实 e.from（不是「来自「X」」）。 */}
                  <span className="fv-meta">
                    <span className="fv-saved">
                      <span className="fv-bookmark" aria-hidden="true" />
                      收藏于 {favoritedAtLabel(e.favoritedAt)}
                    </span>
                    {e.turnedAt.length >= 10 && (
                      <span className="fv-turn">这顿 · {e.turnedAt.slice(5, 10)}</span>
                    )}
                    <span className="fv-from">来自：{e.from}</span>
                  </span>
                  {e.ask && <span className="fv-ask">{e.ask}</span>}
                  <span className="fv-go">
                    {e.detail ? '看这顿的结果 →' : '带着原话回今天 →'}
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
                    <span title="清掉这道菜的踩记录，之后不会再避开它">忘掉</span>
                  </button>
                  <button
                    className="fv-btn fv-btn-danger"
                    type="button"
                    disabled={busy}
                    onClick={() => void remove(e)}
                  >
                    <span title="从收藏里移除，可撤销">取消</span>
                  </button>
                </div>
              </li>
            )
          })}
        </ul>

        {/* 收口行（2026-10-09 · lave2 a5，用户已批）：发丝线 + 真实计数，把
            纸带下沿从"意外结束"变成"有意收尾"。只在有条目时渲染——0 条走
            空态分支，"共 0 条"撞空态禁令（不印 0/短横/undefined）；计数就是
            entries.length，不算造数据。 */}
        <p className="fv-count">
          {shown.length === entries.length
            ? `共 ${entries.length} 条`
            : `筛出 ${shown.length} 条 / 共 ${entries.length} 条`}
        </p>

        {/* 页底：稿的细横线 + 中央花章（同一语汇见周报房 panels.css 的 .wk::after）。 */}
        <div className="fv-footline" aria-hidden="true" />

        {/* 与稿的差异，如实印在页面上：不写出来，看的人会以为「待补」是漏填。
            这一行是说明文字，不是数据，所以不占卡片位、不影响卡片几何。 */}
        <p className="fv-caveat">
          稿上这套版面按 4 张收藏铺排（收藏于 10-28 / 10-26 …）；真实收藏只有
          {' '}{entries.length} 条。「收藏于 · 日期」是后端记下的收藏动作时间，
          在那批还没落这个时间的老收藏上仍是「待补」；「这顿 · MM-DD」是这一轮
          提问的真实时间，两者不是一回事。
          成员行的进度取的是后端真值（口径：该餐营养灯全绿）；进度槽本身按稿不画填充。
        </p>
      </div>

      <aside className="fv-rail">
        {rows && (
          <section className="fv-rail-card">
            <h3 className="fv-rail-h">
              <span className="fv-rail-ico" data-ico="members" aria-hidden="true" />
              按成员推荐
            </h3>
            <ul className="fv-rail-list">
              {rows.members.map((m) => (
                <MemberBlock key={m.id} m={m} />
              ))}
            </ul>
            <p className="fv-rail-note">每一轮方案都按当前生效成员的条件生成。</p>
          </section>
        )}

        {rows && rows.shared.length > 0 && (
          <section className="fv-rail-card">
            <h3 className="fv-rail-h">
              <span className="fv-rail-ico" data-ico="avoid" aria-hidden="true" />
              共同忌口
            </h3>
            <p className="fv-rail-body">{rows.shared.join('、')}</p>
          </section>
        )}

        {fridge.length > 0 && (
          <section className="fv-rail-card">
            <h3 className="fv-rail-h">
              <span className="fv-rail-ico" data-ico="fridge" aria-hidden="true" />
              冰箱现有食材
            </h3>
            {/* 稿上这一块只画了三行（鸡蛋 / 西兰花 / 面条），每行「小图标 + 名称」。
                真实冰箱是四样（familyRows 的 fridge 原样返回，不裁剪），第四样
                （今天是「大蒜」）在稿上没有对应图形。这里**不编图形、也不拿上一行
                的图顶上**——.fv-fridge-ico 对没有切图的 item 走 visibility:hidden 留空，
                只保证名称左缘仍与前三行对齐。 */}
            <ul className="fv-rail-list">
              {fridge.map((item) => (
                <li key={item} className="fv-rail-item">
                  <span className="fv-fridge-ico" data-item={item} aria-hidden="true" />
                  {item}
                </li>
              ))}
            </ul>
          </section>
        )}
      </aside>
    </section>
  )
}

/** 成员块：头像圆 + 姓名 + 条件小签 + 数字行 + 进度槽。
 *
 *  稿实测（CSS px）：头像 26.7×26.7 落在 y213.4；姓名 13.5px 在 y217.7；
 *  条件小签跟随姓名；数字行 y283.7；进度槽 y264 高 12.4、宽 123。
 *
 *  **进度槽不填值**：后端没有"当前进度"这类字段，画任何长度都是编百分比。
 *  槽按稿铺（稿上它就是一条浅色空槽 + 一枚圆点），这里只铺槽与圆点。 */
function MemberBlock({ m }: { m: FamilyMemberRow }) {
  return (
    <li className="fv-mem">
      <div className="fv-mem-top">
        <span className="fv-mem-avatar" aria-hidden="true" />
        <span className="fv-mem-name">{m.name}</span>
        {m.tags.map((t) => (
          <span className="fv-mem-tag" key={t}>
            {t}
          </span>
        ))}
      </div>
      {m.digits.length > 0 && <p className="fv-mem-digits">{m.digits.join(' · ')}</p>}
      <div className="fv-mem-bar" role="presentation">
        <span className="fv-mem-dot" />
      </div>
      <p className="fv-mem-note" title={progressLine(m.progress)}>
        {/* 稿上这一行是「高蛋白 · 助力增肌」（营养师结论）。后端没有这句结论文本，
            编一句就是造内容——所以这里放**后端真有的**那条进度。

            ⚠ 2026-10-14 修：此前写的是「进度待补 · 后端没有这一项」，说错了。
            `nutrition_progress` 后端一直有（reports_route.py
            member_nutrition_progress），只是没被 familyRows 带出来。把"这一屏
            还没接"讲成"后端没有"，等于替后端否认它自己的字段。
            title 挂完整句（含口径出处），槽内只放短版。 */}
        {progressShort(m.progress)}
      </p>
    </li>
  )
}
