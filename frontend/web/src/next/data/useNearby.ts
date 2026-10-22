import { useCallback, useState } from 'react'
import {
  fetchNearby,
  fetchNearbyMap,
  resolveLocation,
  type NearbyMapResult,
  type NearbyQuery,
} from '../../api/client.ts'
import type { NearbyResult } from '../../types.ts'
import { wgs84ToGcj02 } from '../../utils/nearbyNavigation.ts'
import {
  GEO_KEEP_LAST_NOTE,
  GEO_UNAVAILABLE_NOTE,
  GEO_UNSUPPORTED_NOTE,
  geoSelfNote,
  keywordNote,
  nearbyKeyword,
} from './nearbyQuery.ts'

const errText = (e: unknown) => (e instanceof Error ? e.message : String(e))

/** 上次定位到的坐标（GCJ-02）。刷新后仍要记得，所以落 localStorage。 */
const ORIGIN_KEY = 'xsg.nearby.origin'
const MAX_PAGE = 20

function readOrigin(): string {
  try {
    return window.localStorage.getItem(ORIGIN_KEY) || ''
  } catch {
    // 隐私模式下 localStorage 会抛，退化成"本次会话记得"，不影响查询。
    return ''
  }
}

function writeOrigin(value: string): void {
  try {
    if (value) window.localStorage.setItem(ORIGIN_KEY, value)
    else window.localStorage.removeItem(ORIGIN_KEY)
  } catch {
    /* 同上：存不了就只活在内存里 */
  }
}

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
 *
 * 2026-10-21 用户反馈「每次查减脂餐都不记得我的位置」：原实现里 searchByKeyword
 * 会 setOrigin('')，等于每敲一次关键词就把定位丢掉，后端拿不到 location，
 * 高德只能返回空 → 掉进 mock_empty。现改为**定位一次就记住**：
 *   · origin 落 localStorage，刷新后仍在；
 *   · 关键词查询沿用已有坐标，并把 keyword 与 location **一起**发给后端
 *     （原实现在有坐标时把关键词丢了，"减脂餐 + 我的位置"会退化成纯位置查询）；
 *   · 「换一批」= page+1 重查同一组条件，后端 page 早在 1–20 之间就支持。
 */
export function useNearbySearch(draft: string) {
  const [nb, setNb] = useState<NearbyResult | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [note, setNote] = useState<string | null>(null)
  const [locating, setLocating] = useState(false)
  const [mapData, setMapData] = useState<NearbyMapResult | null>(null)
  const [mapError, setMapError] = useState<string | null>(null)
  /** 已拿到的坐标（GCJ-02）。空串 = 从没定位过，此时只按关键词查。 */
  const [origin, setOriginState] = useState(readOrigin)
  /** 第几页；「换一批」+1，到 MAX_PAGE 回 1（后端 page 上限 20）。 */
  const [page, setPage] = useState(1)

  const setOrigin = useCallback((value: string) => {
    setOriginState(value)
    writeOrigin(value)
  }, [])

  const load = useCallback((keyword: string, location: string, targetPage = 1) => {
    setPage(targetPage)
    setLoading(true)
    setError(null)
    setMapData(null)
    setMapError(null)
    /* 关键词与坐标**同时**发：有坐标时也保留 query，否则「减脂餐 + 我的位置」
       会退化成"这一带有什么"，用户问的那道菜就丢了。
       两者都没有时什么都不带，让后端走它自己的默认查询——这里**不编一个
       "中性词"**去凑 query（服务房一直这么用）。
       第一页不传 page，保持旧请求形状。 */
    const params: NearbyQuery = {}
    if (keyword.length > 0) params.query = keyword
    if (location) params.location = location
    if (targetPage > 1) params.page = targetPage
    const payload = Object.keys(params).length > 0 ? params : undefined
    return Promise.allSettled([
      fetchNearby(payload)
        .then((r) => setNb(r))
        .catch((e) => setError(errText(e))),
      fetchNearbyMap(payload)
        .then((r) => setMapData(r))
        .catch((e) => {
          setMapData(null)
          setMapError(errText(e))
        }),
    ])
      .finally(() => setLoading(false))
  }, [])

  /** 关键词路径：**沿用**已记住的坐标，并如实标注这次查的是哪一段。 */
  const searchByKeyword = useCallback(
    (keyword: string, targetPage = 1) => {
      setNote(keywordNote(keyword, Boolean(origin)))
      return load(keyword, origin, targetPage)
    },
    [load, origin],
  )

  /** 换一批：同一组条件（关键词 + 坐标）翻下一页，翻到底回到第 1 页。 */
  const nextPage = useCallback(() => {
    const keyword = nearbyKeyword(draft)
    const target = page >= MAX_PAGE ? 1 : page + 1
    setNote(keywordNote(keyword, Boolean(origin)))
    return load(keyword, origin, target)
  }, [draft, load, origin, page])

  const retry = useCallback(
    () => load(nearbyKeyword(draft), origin, page),
    [load, draft, origin, page],
  )

  /** 只有用户点了「用我的位置」才会走到这里。 */
  const useMyLocation = useCallback(() => {
    const keyword = nearbyKeyword(draft)
    if (!('geolocation' in navigator)) {
      setNote(GEO_UNSUPPORTED_NOTE)
      void load(keyword, origin, 1)
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
        void load(keyword, gcj, 1)
        /* 解析不出地名 = 该处**不放任何句子**（geoSelfNote 返回空串）；
           catch 分支同理——抛错也是"没解析出地名"的一种，不把原因写上页面。
           坐标链路在这两条分支上都不断：上面 load(keyword, gcj, 1) 已经发出去了。 */
        resolveLocation(gcj)
          .then((r) => setNote(geoSelfNote(r.resolved, r.label, r.warning)))
          .catch(() => setNote(''))
          .finally(() => setLocating(false))
      },
      () => {
        /* 拒授权/超时：**不抹掉记住的位置**——上次定位过就继续用它，
           只有从没定位过才退回纯关键词并如实说明。 */
        setLocating(false)
        setNote(origin ? GEO_KEEP_LAST_NOTE : GEO_UNAVAILABLE_NOTE)
        void load(keyword, origin, 1)
      },
      { timeout: 12000, enableHighAccuracy: true, maximumAge: 0 },
    )
  }, [draft, load, origin, setOrigin])

  return {
    nb,
    mapData,
    mapError,
    loading,
    error,
    note,
    locating,
    origin,
    page,
    searchByKeyword,
    nextPage,
    retry,
    useMyLocation,
  }
}
