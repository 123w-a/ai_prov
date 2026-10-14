import { useState } from 'react'
import type { NearbyResult, NearbyRestaurant } from '../../types'
import { enterIntent } from '../data/enterToSend.ts'

/**
 * 附近餐厅块（服务房分区二）。
 *
 * 三条诚实线（与 lave 商定的降级矩阵）：
 *   · source / warning 是后端原话，常驻列表上方——现在是 mock 就写模拟，
 *     不把模拟参考餐厅画成真数据；
 *   · amap_configured=false 时写「地图能力未配置」，不画地图或地图占位；
 *   · guardrail 是点单提醒，紧贴餐厅信息展示，不藏进详情。
 * 查询走后端 /api/nearby 的 query 参数，前端不做定位（无 JS Key 也无从画）。
 *
 * 2026-10-12 两处**可选**参数（首屏「出去吃」入口条用），服务房调用点不传、
 * 行为与文案一字不变：
 *   · emptyText —— 无结果时那句话由调用方给（首屏要「这一带没查到，换个词试试」）；
 *   · navUrl    —— 给一家店生成高德外链（没坐标返回 null，此时不渲染链接，
 *                  **不编一个假的 href**）。导航仍是外链，页面里不画地图。
 */
export function NearbyList({
  nb,
  loading,
  error,
  onSearch,
  onRetry,
  emptyText,
  navUrl,
}: {
  nb: NearbyResult | null
  loading: boolean
  error: string | null
  onSearch: (query: string) => void
  onRetry: () => void
  /** 无结果时的原话。不给就用服务房那句。 */
  emptyText?: string
  /** 由调用方算好的导航外链（它才知道有没有起点坐标）。返回 null = 这家不给链接。 */
  navUrl?: (r: NearbyRestaurant) => string | null
}) {
  const [q, setQ] = useState('')

  return (
    <>
      <form
        className="sv-find"
        onSubmit={(e) => {
          e.preventDefault()
          onSearch(q.trim())
        }}
        /* Enter = 查询（用户原话：「按一下 enter 键就应该要开始了」）。判定复用
           data/enterToSend.ts，与今晚房、身体档案房同一份；'newline'/'ignore' 放行不拦
           （输入法拼字回车在这里尤其危险：那是选词确认，不等于"我要查这一家"）。
           拦下后走 requestSubmit——本表单现成的 onSubmit，查询中或空词不提交。 */
        onKeyDown={(e) => {
          if (enterIntent(e.nativeEvent) !== 'send') return
          e.preventDefault()
          if (loading || !q.trim()) return
          e.currentTarget.requestSubmit()
        }}
      >
        <input
          className="sv-find-in"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="查一家：菜系、店名或想吃的"
          aria-label="查询附近餐厅"
        />
        <button className="fv-btn" type="submit" disabled={loading}>
          {loading ? '查询中…' : '查询'}
        </button>
      </form>

      {nb && (
        <p className="sv-src">
          {nb.source === 'mock' ? '模拟参考数据' : `数据来源 ${nb.source}`}
          {!nb.amap_configured && ' · 地图能力未配置'}
          {nb.warning ? ` · ${nb.warning}` : ''}
        </p>
      )}

      {error && (
        <p className="sv-err">
          附近餐厅读取失败：{error}{' '}
          <button className="fv-btn" type="button" onClick={onRetry}>
            重试
          </button>
        </p>
      )}

      {!error && loading && <p className="sv-loading">正在查询…</p>}

      {!error && !loading && nb && nb.restaurants.length === 0 && (
        <p className="sv-loading">{emptyText ?? '没有查到符合条件的餐厅，换个词试试。'}</p>
      )}

      {!error && nb && nb.restaurants.length > 0 && (
        <ul className="sv-rest">
          {nb.restaurants.map((r, i) => {
            const href = navUrl ? navUrl(r) : null
            return (
              <li key={`${r.name}-${i}`}>
                <div className="sv-rest-h">
                  <span className="sv-rest-name">{r.name}</span>
                  <span className="sv-rest-meta">
                    {r.cuisine}
                    {r.avg_price != null && ` · 人均 ${r.avg_price} 元`}
                    {r.distance_km != null && ` · ${r.distance_km} km`}
                  </span>
                  {href && (
                    <a className="sv-rest-nav" href={href} target="_blank" rel="noreferrer">
                      高德导航
                    </a>
                  )}
                </div>
                {r.guardrail && (
                  <p className="sv-rest-rg">
                    <span className="sv-rest-rg-k">点单提醒</span>
                    {r.guardrail}
                  </p>
                )}
              </li>
            )
          })}
        </ul>
      )}
    </>
  )
}
