import { useEffect, useState } from 'react'
import { NearbyRestaurantMap } from '../../components/NearbyRestaurantMap.tsx'
import { NearbyList } from './NearbyList.tsx'
import { useNearbySearch } from '../data/useNearby.ts'
import { NEARBY_EMPTY_TEXT, navUrlFor, nearbyKeyword } from '../data/nearbyQuery.ts'

/**
 * 「出去吃」入口条 + 就地展开的附近餐厅。
 * 地图只使用 /api/nearby/map 的真实坐标；mock 和空结果不补造 marker。
 * 列表复用 NearbyList，额外传入空结果文案、导航外链和地图选中联动。
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
  const [selectedIndex, setSelectedIndex] = useState(0)
  const {
    nb,
    mapData,
    mapError,
    loading,
    error,
    note,
    locating,
    origin,
    searchByKeyword,
    retry,
    useMyLocation,
  } = useNearbySearch(draft)

  const mapRestaurants = mapData?.markers ?? []
  const mapCenter = mapData?.center ? `${mapData.center.lng},${mapData.center.lat}` : origin
  const mapNotice =
    mapData && mapData.markers.length === 0 && mapData.source !== 'amap'
      ? '当前没有真实餐厅坐标，地图不展示餐厅标记'
      : ''

  useEffect(() => {
    setSelectedIndex(0)
  }, [nb])

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
          <div className="ask-nearby-workbench">
            <div className="ask-nearby-map">
              <NearbyRestaurantMap
                restaurants={mapRestaurants}
                origin={origin}
                center={mapCenter}
                coordinateSystem="gcj02"
                selectedIndex={selectedIndex}
                onSelectRestaurant={setSelectedIndex}
                variant="embedded"
                showLegend={false}
                showSelection={false}
                emptyMessage="先点「用我的位置」，或查询到真实高德结果后显示地图"
              />
              {(mapNotice || mapError) && (
                <p className="ask-nearby-map-notice">
                  {mapError ? `地图数据读取失败：${mapError}` : mapNotice}
                </p>
              )}
            </div>
            <div className="ask-nearby-list-pane">
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
                selectedIndex={selectedIndex}
                onSelect={setSelectedIndex}
              />
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
