import { useCallback, useEffect, useRef, useState } from 'react'
import { fetchNearby, fetchServiceVision } from '../../api/client'
import type { NearbyResult, ServiceVision } from '../../types'
import { wgs84ToGcj02 } from '../../utils/nearbyNavigation'
import { VisionRoadmap } from '../blocks/VisionRoadmap.tsx'
import { NearbyList } from '../blocks/NearbyList.tsx'

/**
 * 服务房间（2026-09-30 户型图第 5 步 · 落地顺序最后一间）。
 *
 * 与 lave（gpt-5.6-sol）商定方案 C：同房双分区——
 *   分区一「规划」：GET /api/service/vision 的愿景与路线图（零凭据依赖，恒可渲染）；
 *   分区二「附近餐厅」：GET /api/nearby 的列表（不做地图：前端没有
 *     VITE_AMAP_JS_KEY，画不了高德 JS；后端 POI 降级时 source/warning 如实上墙）。
 * POST /api/service/preview 首切片不接——没实测过的行为不进界面。
 *
 * 两个分区各自持加载 / 失败 / 成功三态：愿景挂了不能拖累附近餐厅，反之亦然。
 */
const errText = (e: unknown) => (e instanceof Error ? e.message : String(e))

export default function ServiceView() {
  const [vision, setVision] = useState<ServiceVision | null>(null)
  const [vErr, setVErr] = useState<string | null>(null)
  const [vLoading, setVLoading] = useState(true)

  const [nb, setNb] = useState<NearbyResult | null>(null)
  const [nErr, setNErr] = useState<string | null>(null)
  const [nLoading, setNLoading] = useState(true)
  /** 已拿到的坐标（GCJ-02）。空串 = 还没定位，此时后端只能按关键词/城市查。 */
  const [origin, setOrigin] = useState('')
  /** 自动定位只做一次：StrictMode 下 effect 会跑两遍，不加这个会重复请求定位。 */
  const autoLocatedRef = useRef(false)

  const loadVision = useCallback(() => {
    setVLoading(true)
    setVErr(null)
    fetchServiceVision()
      .then((v) => setVision(v))
      .catch((e) => setVErr(errText(e)))
      .finally(() => setVLoading(false))
  }, [])

  const loadNearby = useCallback((query?: string, location?: string) => {
    setNLoading(true)
    setNErr(null)
    /* 三档：有坐标 → 周边搜索（结果带真实距离）；只有词 → 关键词搜索；
     * 都没有 → 后端默认查询。**有坐标时带上词**，才能"按关键词在附近找"。 */
    const params = location
      ? { location, ...(query ? { query } : {}) }
      : query
        ? { query }
        : undefined
    fetchNearby(params)
      .then((r) => setNb(r))
      .catch((e) => setNErr(errText(e)))
      .finally(() => setNLoading(false))
  }, [])

  useEffect(() => {
    loadVision()
    /* 附近餐厅的首次查询**不在这里发**：交给下面的定位流程。
     * 否则会"先按无坐标查一次（返回外地店）→ 定位回来再查一次"，中间闪一下。 */
  }, [loadVision])

  /* ── 进服务房就定位一次（2026-10-19）────────────────────────────────
   * 为什么这里要打破 useNearbySearch 那条「默认路径不请求系统定位」的约定：
   * 那条约定是给**首屏折叠条**定的——用户可能只想点菜，一进来就弹权限框很打扰。
   * 而「服务」房间的存在意义就是找店，没有坐标时后端只能按关键词/城市查，
   * 实测会返回北京的店（高德按请求 IP 兜底），距离也全是空的——那不叫附近餐厅。
   *
   * 三条降级：定位成功 → 用坐标查；拒授权/超时 → 回落无坐标查；不支持 → 同上。
   * 任何一条都不报错，用户仍可再点「用我的位置」重试。 */
  useEffect(() => {
    if (autoLocatedRef.current) return
    autoLocatedRef.current = true
    const fallback = () => loadNearby()
    if (typeof navigator === 'undefined' || !('geolocation' in navigator)) {
      fallback()
      return
    }
    navigator.geolocation.getCurrentPosition(
      (pos) => {
        /* 浏览器给 WGS-84、高德用 GCJ-02，必须转换，否则位置会偏几百米。 */
        const gcj = wgs84ToGcj02(
          `${pos.coords.longitude.toFixed(6)},${pos.coords.latitude.toFixed(6)}`,
        )
        setOrigin(gcj)
        loadNearby(undefined, gcj)
      },
      fallback,
      { timeout: 12000, enableHighAccuracy: true, maximumAge: 0 },
    )
  }, [loadNearby])

  /* 副行按 status 原值口径给中文（planned → 远期规划，与接口 messages 同义）；
     没读到前只写「正在读取」，不预设一个没读到的名字。 */
  const sub = vision
    ? `${vision.name} · ${vision.status === 'planned' ? '远期规划' : vision.status} · 当前仅提供能力预览`
    : vLoading
      ? '正在读取服务信息…'
      : '服务信息没有读出来'

  return (
    <section className="sv" data-room="svc">
      <div className="sv-head">
        <h2 className="sv-h">服务</h2>
        <p className="sv-sub">{sub}</p>
      </div>

      <div className="sv-sec">
        <h3 className="sv-sec-h">服务规划</h3>
        {vErr ? (
          <>
            <p className="sv-err">服务规划读取失败：{vErr}</p>
            <button className="fv-btn" type="button" onClick={loadVision}>
              重试
            </button>
          </>
        ) : vLoading ? (
          <p className="sv-loading">正在读取服务规划…</p>
        ) : vision ? (
          <VisionRoadmap v={vision} />
        ) : null}
      </div>

      <div className="sv-sec">
        <h3 className="sv-sec-h">附近餐厅</h3>
        <NearbyList
          nb={nb}
          loading={nLoading}
          error={nErr}
          onSearch={(q) => loadNearby(q || undefined, origin)}
          onRetry={() => loadNearby(undefined, origin)}
        />
      </div>
    </section>
  )
}


