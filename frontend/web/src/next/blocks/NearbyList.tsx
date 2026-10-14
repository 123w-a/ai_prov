import { useState } from 'react'
import type { NearbyResult } from '../../types'

/**
 * 附近餐厅块（服务房分区二）。
 *
 * 三条诚实线（与 lave 商定的降级矩阵）：
 *   · source / warning 是后端原话，常驻列表上方——现在是 mock 就写模拟，
 *     不把模拟参考餐厅画成真数据；
 *   · amap_configured=false 时写「地图能力未配置」，不画地图或地图占位；
 *   · guardrail 是点单提醒，紧贴餐厅信息展示，不藏进详情。
 * 查询走后端 /api/nearby 的 query 参数，前端不做定位（无 JS Key 也无从画）。
 */
export function NearbyList({
  nb,
  loading,
  error,
  onSearch,
  onRetry,
}: {
  nb: NearbyResult | null
  loading: boolean
  error: string | null
  onSearch: (query: string) => void
  onRetry: () => void
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
        <p className="sv-loading">没有查到符合条件的餐厅，换个词试试。</p>
      )}

      {!error && nb && nb.restaurants.length > 0 && (
        <ul className="sv-rest">
          {nb.restaurants.map((r, i) => (
            <li key={`${r.name}-${i}`}>
              <div className="sv-rest-h">
                <span className="sv-rest-name">{r.name}</span>
                <span className="sv-rest-meta">
                  {r.cuisine}
                  {r.avg_price != null && ` · 人均 ${r.avg_price} 元`}
                  {r.distance_km != null && ` · ${r.distance_km} km`}
                </span>
              </div>
              {r.guardrail && (
                <p className="sv-rest-rg">
                  <span className="sv-rest-rg-k">点单提醒</span>
                  {r.guardrail}
                </p>
              )}
            </li>
          ))}
        </ul>
      )}
    </>
  )
}
