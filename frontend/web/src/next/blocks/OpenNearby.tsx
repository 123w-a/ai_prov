import { useState } from 'react'
import { NearbyList } from './NearbyList.tsx'
import { useNearbySearch } from '../data/useNearby.ts'
import { NEARBY_EMPTY_TEXT, navUrlFor, nearbyKeyword } from '../data/nearbyQuery.ts'

/**
 * 「出去吃」入口条 + 就地展开的附近餐厅（2026-10-12）。
 *
 * 位置由 AskView 决定：书写面 `.composer-panel` **正下方、通栏**，展开时
 * 「试试这样问」与示例卡就地让位（同一位置互换），所以示例不会被挤到折叠线下面。
 *
 * 这个文件只管**渲染与展开/收起**；查询与定位的状态机在 data/useNearby.ts，
 * 那里写着三条边界：默认不请求系统定位、拿不到位置不假装已定位、失败就地重试。
 *
 * **不画地图**：前端没有高德 JS Key（服务房同一条边界），导航只给外链。
 * 餐厅列表复用服务房那份 NearbyList（来源/warning/guardrail/空结果全套），
 * 只多传两个可选参数（空结果文案、导航外链），服务房调用点一个字没改。
 */
export function OpenNearby({
  draft,
  onOpenChange,
}: {
  /** 输入框里此刻的草稿：第一次展开拿它当关键词，不额外请求定位。 */
  draft: string
  /** 展开/收起要通知 AskView：示例卡在展开时收起（同一位置互换）。 */
  onOpenChange: (open: boolean) => void
}) {
  const [open, setOpen] = useState(false)
  const { nb, loading, error, note, locating, origin, searchByKeyword, retry, useMyLocation } =
    useNearbySearch(draft)

  const toggle = () => {
    if (open) {
      setOpen(false)
      onOpenChange(false)
      return
    }
    setOpen(true)
    onOpenChange(true)
    // 第一次展开**不请求系统定位**：先用输入框里已有的话查（没有就按后端默认查）。
    void searchByKeyword(nearbyKeyword(draft))
  }

  return (
    <div className="ask-nearby">
      <button
        className={`ask-nearby-bar${open ? ' is-open' : ''}`}
        type="button"
        aria-expanded={open}
        onClick={toggle}
      >
        {/* 单个定位针（细描边 1.4、无填充、currentColor）。刻意**不做**"针里嵌碗"：
            那层细节在 22px 下糊成一团，方案里已经裁决过。 */}
        <svg className="ask-nearby-pin" viewBox="0 0 24 24" aria-hidden="true" focusable="false">
          <g
            fill="none"
            stroke="currentColor"
            strokeWidth="1.4"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <path d="M12 21.1c0 0 6.6-7 6.6-11.2a6.6 6.6 0 1 0-13.2 0c0 4.2 6.6 11.2 6.6 11.2Z" />
            <circle cx="12" cy="9.9" r="2.3" />
          </g>
        </svg>
        <span className="ask-nearby-text">不想做？看看附近能吃什么</span>
        <span className="ask-nearby-caret" aria-hidden="true">
          {open ? '收起' : '展开'}
        </span>
      </button>

      {open && (
        <div className="ask-nearby-body">
          <div className="ask-nearby-tools">
            <button className="fv-btn" type="button" onClick={useMyLocation} disabled={locating}>
              {locating ? '定位中…' : '用我的位置'}
            </button>
            {note && (
              <span className="ask-nearby-note" role="status">
                {note}
              </span>
            )}
          </div>
          <NearbyList
            nb={nb}
            loading={loading}
            error={error}
            emptyText={NEARBY_EMPTY_TEXT}
            /* 没定位时 origin 是空串：`buildAmapNavigationUrl` 只带目的地、
               不带假起点（它自己判 hasValidOrigin）。 */
            navUrl={(r) => navUrlFor(r, origin)}
            onSearch={(q) => void searchByKeyword(nearbyKeyword(q))}
            onRetry={retry}
          />
        </div>
      )}
    </div>
  )
}
