import { useEffect, useRef, useState } from 'react'
import type { NearbyResult, NearbyRestaurant } from '../../types'
import { enterIntent } from '../data/enterToSend.ts'

/**
 * 附近餐厅块（服务房分区二）。
 * source / warning / guardrail 均按后端原话展示；没有坐标就不生成导航链接。
 * 首屏可额外传入空结果文案、导航外链和地图选中联动，服务房调用保持原行为。
 */
export function NearbyList({
  nb,
  loading,
  error,
  onSearch,
  onRetry,
  emptyText,
  navUrl,
  selectedIndex,
  onSelect,
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
  /** 与地图 marker 联动的选中项；不传时保持原列表行为。 */
  selectedIndex?: number | null
  onSelect?: (index: number) => void
}) {
  const [q, setQ] = useState('')
  const itemRefs = useRef<Array<HTMLLIElement | null>>([])

  useEffect(() => {
    if (selectedIndex == null) return
    itemRefs.current[selectedIndex]?.scrollIntoView({ block: 'nearest' })
  }, [selectedIndex])

  return (
    <>
      <form
        className="sv-find"
        onSubmit={(e) => {
          e.preventDefault()
          onSearch(q.trim())
        }}
        /* Enter 走统一的 enterToSend 判定，避开输入法选词回车。 */
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
              <li
                key={`${r.name}-${i}`}
                ref={(node) => {
                  itemRefs.current[i] = node
                }}
                className={selectedIndex === i ? 'is-selected' : undefined}
                role={onSelect ? 'button' : undefined}
                tabIndex={onSelect ? 0 : undefined}
                onClick={onSelect ? () => onSelect(i) : undefined}
                onKeyDown={
                  onSelect
                    ? (event) => {
                        if (event.key !== 'Enter' && event.key !== ' ') return
                        event.preventDefault()
                        onSelect(i)
                      }
                    : undefined
                }
              >
                <div className="sv-rest-h">
                  {selectedIndex != null && (
                    <span className="sv-rest-no" aria-hidden="true">
                      {i + 1}
                    </span>
                  )}
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
