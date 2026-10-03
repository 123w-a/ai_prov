import { useCallback, useEffect, useState } from 'react'
import { fetchNearby, fetchServiceVision } from '../../api/client'
import type { NearbyResult, ServiceVision } from '../../types'
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

  const loadVision = useCallback(() => {
    setVLoading(true)
    setVErr(null)
    fetchServiceVision()
      .then((v) => setVision(v))
      .catch((e) => setVErr(errText(e)))
      .finally(() => setVLoading(false))
  }, [])

  const loadNearby = useCallback((query?: string) => {
    setNLoading(true)
    setNErr(null)
    fetchNearby(query ? { query } : undefined)
      .then((r) => setNb(r))
      .catch((e) => setNErr(errText(e)))
      .finally(() => setNLoading(false))
  }, [])

  useEffect(() => {
    loadVision()
    loadNearby()
  }, [loadVision, loadNearby])

  /* 副行按 status 原值口径给中文（planned → 远期规划，与接口 messages 同义）；
     没读到前只写「正在读取」，不预设一个没读到的名字。 */
  const sub = vision
    ? `${vision.name} · ${vision.status === 'planned' ? '远期规划' : vision.status} · 当前仅提供能力预览`
    : vLoading
      ? '正在读取服务信息…'
      : '服务信息没有读出来'

  return (
    <section className="sv" data-room="svc">
      <h2 className="sv-h">服务</h2>
      <p className="sv-sub">{sub}</p>

      <div className="sv-sec">
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
        <div className="sv-k">附近餐厅</div>
        <NearbyList
          nb={nb}
          loading={nLoading}
          error={nErr}
          onSearch={(q) => loadNearby(q || undefined)}
          onRetry={() => loadNearby()}
        />
      </div>
    </section>
  )
}
