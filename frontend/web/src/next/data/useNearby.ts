import { useCallback, useState } from 'react'
import { fetchNearby, resolveLocation } from '../../api/client.ts'
import type { NearbyResult } from '../../types.ts'
import { wgs84ToGcj02 } from '../../utils/nearbyNavigation.ts'
import {
  GEO_UNAVAILABLE_NOTE,
  GEO_UNSUPPORTED_NOTE,
  geoSelfNote,
  keywordNote,
  nearbyKeyword,
} from './nearbyQuery.ts'

const errText = (e: unknown) => (e instanceof Error ? e.message : String(e))

/**
 * 「附近餐厅」这一次查询的状态机（2026-10-12）。
 *
 * 从 blocks/OpenNearby.tsx 拆出来有两个理由，都写下来免得下一个人合回去：
 *   ① check-layers 的规模上限对 blocks 是 **150 行**，逻辑留在组件里就一定越线；
 *   ② 这里真正需要被看懂的是**降级链**：关键词查询是默认路径，系统定位只能由
 *      用户主动触发；拿不到位置、解析不出地名各自说什么话，全都在这一个文件里。
 *
 * 三条不许含糊：
 *   · 默认路径**不请求系统定位**（getCurrentPosition 只在 useMyLocation 里被调用）；
 *   · 拿不到位置 → note = GEO_UNAVAILABLE_NOTE 且**保持关键词查询**，不假装已定位；
 *   · 拿到坐标但解析不出地名 → note 为空串（**页面上不放原因**，2026-10-12 用户裁决），
 *     查询照旧按坐标走，成功态文案只在真 resolved 时出现；
 *   · 接口失败 → error 原话交给 NearbyList 就地报错 + 重试，不吞。
 */
export function useNearbySearch(draft: string) {
  const [nb, setNb] = useState<NearbyResult | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [note, setNote] = useState<string | null>(null)
  const [locating, setLocating] = useState(false)
  /** 已拿到的坐标（GCJ-02）。空串 = 没定位，此时只按关键词查。 */
  const [origin, setOrigin] = useState('')

  const load = useCallback((keyword: string, location: string) => {
    setLoading(true)
    setError(null)
    /* 不带 query 也不带 location ⇒ 后端走它自己的默认查询（服务房一直这么用）。
       这里**不编一个"中性词"**去凑 query。 */
    const params = location
      ? { location }
      : keyword.length > 0
        ? { query: keyword }
        : undefined
    return fetchNearby(params)
      .then((r) => setNb(r))
      .catch((e) => setError(errText(e)))
      .finally(() => setLoading(false))
  }, [])

  /** 关键词路径：清掉坐标，如实标注这次是按关键词查的。 */
  const searchByKeyword = useCallback(
    (keyword: string) => {
      setOrigin('')
      setNote(keywordNote(keyword))
      return load(keyword, '')
    },
    [load],
  )

  const retry = useCallback(() => load(nearbyKeyword(draft), origin), [load, draft, origin])

  /** 只有用户点了「用我的位置」才会走到这里。 */
  const useMyLocation = useCallback(() => {
    const keyword = nearbyKeyword(draft)
    if (!('geolocation' in navigator)) {
      setOrigin('')
      setNote(GEO_UNSUPPORTED_NOTE)
      void load(keyword, '')
      return
    }
    setLocating(true)
    navigator.geolocation.getCurrentPosition(
      (pos) => {
        /* 浏览器给的是 WGS-84、高德用 GCJ-02——与老前端/服务房同一个转换函数。 */
        const gcj = wgs84ToGcj02(
          `${pos.coords.longitude.toFixed(6)},${pos.coords.latitude.toFixed(6)}`,
        )
        setOrigin(gcj)
        void load(keyword, gcj)
        /* 解析不出地名 = 该处**不放任何句子**（geoSelfNote 返回空串）；
           catch 分支同理——抛错也是"没解析出地名"的一种，不把原因写上页面。
           坐标链路在这两条分支上都不断：上面 load(keyword, gcj) 已经发出去了。 */
        resolveLocation(gcj)
          .then((r) => setNote(geoSelfNote(r.resolved, r.label, r.warning)))
          .catch(() => setNote(''))
          .finally(() => setLocating(false))
      },
      () => {
        // 拒授权/超时：降级回关键词，并把"没拿到位置"写清楚。
        setLocating(false)
        setOrigin('')
        setNote(GEO_UNAVAILABLE_NOTE)
        void load(keyword, '')
      },
      { timeout: 12000, enableHighAccuracy: true, maximumAge: 0 },
    )
  }, [draft, load])

  return { nb, loading, error, note, locating, origin, searchByKeyword, retry, useMyLocation }
}
