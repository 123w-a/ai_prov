import { useEffect, useMemo, useRef, useState } from 'react'
import type { NearbyRestaurant } from '../types'
import {
  buildAmapNavigationUrl,
  wgs84ToGcj02,
  type CoordinateSystem,
} from '../utils/nearbyNavigation'

type Coordinate = [number, number]

type AMapMarker = {
  on: (event: string, handler: () => void) => void
}

type AMapInstance = {
  add: (overlays: unknown | unknown[]) => void
  destroy: () => void
  panTo: (position: Coordinate) => void
  remove: (overlays: unknown[]) => void
  resize: () => void
  setFitView: (overlays?: unknown[], immediately?: boolean, avoid?: number[], maxZoom?: number) => void
}

type AMapNamespace = {
  Map: new (container: HTMLElement, options: Record<string, unknown>) => AMapInstance
  Marker: new (options: Record<string, unknown>) => AMapMarker
  Pixel: new (x: number, y: number) => unknown
}

type AMapLoader = {
  load: (options: { key: string; version: string }) => Promise<AMapNamespace>
}

type AMapWindow = Window & {
  AMapLoader?: AMapLoader
  _AMapSecurityConfig?: { securityJsCode: string }
}

interface Props {
  restaurants: NearbyRestaurant[]
  origin: string
  /** 地图中心点；与 origin 使用同一种坐标系。 */
  center?: string
  /** 旧页和 ChatArea 传 WGS-84；新版附近餐厅传 GCJ-02。 */
  coordinateSystem?: CoordinateSystem
  /** 外部列表控制选中项时使用；不传则沿用组件内部选择。 */
  selectedIndex?: number
  onSelectRestaurant?: (index: number) => void
  variant?: 'default' | 'embedded'
  showLegend?: boolean
  showSelection?: boolean
  emptyMessage?: string
}

const AMAP_LOADER_ID = 'amap-jsapi-loader'
let amapLoaderPromise: Promise<AMapNamespace> | null = null

const parseCoordinate = (value: string): Coordinate | null => {
  const [lng, lat] = value.split(',').map((part) => Number(part.trim()))
  if (!Number.isFinite(lng) || !Number.isFinite(lat)) return null
  if (lng < -180 || lng > 180 || lat < -90 || lat > 90) return null
  return [lng, lat]
}

const restaurantCoordinate = (restaurant: NearbyRestaurant): Coordinate | null => {
  if (
    typeof restaurant.lng !== 'number' ||
    typeof restaurant.lat !== 'number' ||
    !Number.isFinite(restaurant.lng) ||
    !Number.isFinite(restaurant.lat)
  ) {
    return null
  }
  return [restaurant.lng, restaurant.lat]
}

const loadAmap = (key: string, securityCode: string): Promise<AMapNamespace> => {
  if (amapLoaderPromise) return amapLoaderPromise

  const amapWindow = window as AMapWindow
  amapWindow._AMapSecurityConfig = { securityJsCode: securityCode }

  amapLoaderPromise = new Promise<AMapLoader>((resolve, reject) => {
    if (amapWindow.AMapLoader) {
      resolve(amapWindow.AMapLoader)
      return
    }

    const existing = document.getElementById(AMAP_LOADER_ID) as HTMLScriptElement | null
    const script = existing ?? document.createElement('script')
    if (!existing) {
      script.id = AMAP_LOADER_ID
      script.src = 'https://webapi.amap.com/loader.js'
      script.async = true
      document.head.appendChild(script)
    }
    script.addEventListener('load', () => {
      if (amapWindow.AMapLoader) resolve(amapWindow.AMapLoader)
      else reject(new Error('高德地图加载器未就绪'))
    }, { once: true })
    script.addEventListener('error', () => reject(new Error('高德地图脚本加载失败')), { once: true })
  }).then((loader) => loader.load({ key, version: '2.0' }))

  return amapLoaderPromise
}

export function NearbyRestaurantMap({
  restaurants,
  origin,
  center = '',
  coordinateSystem = 'wgs84',
  selectedIndex: controlledSelectedIndex,
  onSelectRestaurant,
  variant = 'default',
  showLegend = true,
  showSelection = true,
  emptyMessage = '当前批次没有可用于地图展示的坐标',
}: Props) {
  const containerRef = useRef<HTMLDivElement>(null)
  const mapRef = useRef<AMapInstance | null>(null)
  const amapRef = useRef<AMapNamespace | null>(null)
  const markersRef = useRef<AMapMarker[]>([])
  const lastSelectionRef = useRef(controlledSelectedIndex ?? 0)
  const [mapReady, setMapReady] = useState(false)
  const [mapError, setMapError] = useState('')
  const [internalSelectedIndex, setInternalSelectedIndex] = useState(0)
  const selectedIndex = controlledSelectedIndex ?? internalSelectedIndex

  const apiKey = (import.meta.env.VITE_AMAP_JS_KEY as string | undefined)?.trim() ?? ''
  const securityCode = (import.meta.env.VITE_AMAP_SECURITY_CODE as string | undefined)?.trim() ?? ''
  const originCoordinate = useMemo(
    () => parseCoordinate(coordinateSystem === 'gcj02' ? origin : wgs84ToGcj02(origin)),
    [origin, coordinateSystem],
  )
  const centerCoordinate = useMemo(
    () => parseCoordinate(coordinateSystem === 'gcj02' ? center : wgs84ToGcj02(center)),
    [center, coordinateSystem],
  )
  const validRestaurants = useMemo(
    () => restaurants
      .map((restaurant, sourceIndex) => ({
        restaurant,
        sourceIndex,
        coordinate: restaurantCoordinate(restaurant),
      }))
      .filter((item): item is typeof item & { coordinate: Coordinate } => item.coordinate !== null),
    [restaurants],
  )
  const initialCenter = useMemo(
    () => centerCoordinate ?? originCoordinate ?? validRestaurants[0]?.coordinate ?? null,
    [centerCoordinate, originCoordinate, validRestaurants],
  )
  const initialCenterKey = initialCenter ? initialCenter.join(',') : ''

  useEffect(() => {
    if (controlledSelectedIndex === undefined) setInternalSelectedIndex(0)
  }, [restaurants, controlledSelectedIndex])

  useEffect(() => {
    if (!apiKey || !securityCode) {
      setMapError('地图视图待配置高德 Web 端 JS API Key 与安全密钥')
      return
    }
    if (!containerRef.current) return
    if (!initialCenter) {
      setMapReady(false)
      setMapError(emptyMessage)
      return
    }

    let cancelled = false
    let resizeObserver: ResizeObserver | null = null
    setMapReady(false)
    void loadAmap(apiKey, securityCode)
      .then((AMap) => {
        if (cancelled || !containerRef.current) return
        amapRef.current = AMap
        const map = new AMap.Map(containerRef.current, {
          center: initialCenter,
          zoom: 15,
          mapStyle: 'amap://styles/normal',
          viewMode: '2D',
        })
        mapRef.current = map
        resizeObserver = new ResizeObserver(() => map.resize())
        resizeObserver.observe(containerRef.current)
        setMapReady(true)
        setMapError('')
      })
      .catch((error: unknown) => {
        if (!cancelled) {
          setMapError(error instanceof Error ? error.message : '高德地图加载失败')
        }
      })

    return () => {
      cancelled = true
      resizeObserver?.disconnect()
      mapRef.current?.destroy()
      mapRef.current = null
      amapRef.current = null
      markersRef.current = []
    }
  }, [apiKey, securityCode, initialCenterKey, emptyMessage])

  useEffect(() => {
    const map = mapRef.current
    const AMap = amapRef.current
    if (!mapReady || !map || !AMap) return

    if (markersRef.current.length > 0) {
      map.remove(markersRef.current)
    }

    const nextMarkers: AMapMarker[] = []
    if (originCoordinate) {
      nextMarkers.push(new AMap.Marker({
        position: originCoordinate,
        title: '我的位置',
        zIndex: 120,
        /* 与 map.css 的 .amap-origin-marker 对齐：那边是 20×20（border-box），
           所以偏移取半宽 -10，蓝点中心才落在真实坐标上。改一边必须同步改另一边。 */
        offset: new AMap.Pixel(-10, -10),
        content: '<div class="amap-origin-marker" aria-label="我的位置"></div>',
      }))
    }

    validRestaurants.forEach((item) => {
      const marker = new AMap.Marker({
        position: item.coordinate,
        title: item.restaurant.name,
        zIndex: 100,
        offset: new AMap.Pixel(-17, -38),
        content: `<div class="amap-restaurant-marker"><span>${item.sourceIndex + 1}</span></div>`,
      })
      marker.on('click', () => {
        if (controlledSelectedIndex === undefined) {
          setInternalSelectedIndex(item.sourceIndex)
        }
        onSelectRestaurant?.(item.sourceIndex)
        lastSelectionRef.current = item.sourceIndex
        map.panTo(item.coordinate)
      })
      nextMarkers.push(marker)
    })

    if (nextMarkers.length > 0) map.add(nextMarkers)
    markersRef.current = nextMarkers
    if (validRestaurants.length > 0) {
      map.setFitView(nextMarkers, false, [54, 54, 54, 54], 17)
    } else if (initialCenter) {
      map.panTo(initialCenter)
    }
  }, [
    mapReady,
    originCoordinate,
    validRestaurants,
    controlledSelectedIndex,
    onSelectRestaurant,
    initialCenter,
  ])

  useEffect(() => {
    if (!mapReady || lastSelectionRef.current === selectedIndex) return
    const target = validRestaurants.find((item) => item.sourceIndex === selectedIndex)
    if (!target) return
    lastSelectionRef.current = selectedIndex
    mapRef.current?.panTo(target.coordinate)
  }, [mapReady, selectedIndex, validRestaurants])

  const selected =
    validRestaurants.find((item) => item.sourceIndex === selectedIndex) ?? validRestaurants[0]
  const navigationUrl = selected
    ? buildAmapNavigationUrl(selected.restaurant, origin, undefined, coordinateSystem)
    : null

  const focusRestaurant = (index: number) => {
    const target = validRestaurants.find((item) => item.sourceIndex === index)
    if (!target) return
    if (controlledSelectedIndex === undefined) setInternalSelectedIndex(index)
    onSelectRestaurant?.(index)
    lastSelectionRef.current = index
    mapRef.current?.panTo(target.coordinate)
  }

  return (
    <div className={`nearby-map-shell${variant === 'embedded' ? ' is-embedded' : ''}`}>
      <div
        ref={containerRef}
        className={mapError ? 'nearby-map-canvas unavailable' : 'nearby-map-canvas'}
        aria-label="附近餐厅高德地图"
      >
        {!mapReady && !mapError && <span className="nearby-map-status">地图加载中…</span>}
        {mapError && <span className="nearby-map-status">{mapError}</span>}
      </div>
      {showLegend && mapReady && validRestaurants.length > 0 && (
        <div className="nearby-map-legend" aria-label="地图餐厅标记">
          {validRestaurants.map((item) => (
            <button
              type="button"
              key={`${item.restaurant.name}-${item.sourceIndex}`}
              className={selectedIndex === item.sourceIndex ? 'active' : ''}
              onClick={() => focusRestaurant(item.sourceIndex)}
              title={`在地图上查看 ${item.restaurant.name}`}
            >
              <span>{item.sourceIndex + 1}</span>
              {item.restaurant.name}
            </button>
          ))}
        </div>
      )}
      {showSelection && mapReady && selected && (
        <div className="nearby-map-selection" aria-live="polite">
          <span className="nearby-map-number">{selected.sourceIndex + 1}</span>
          <div>
            <strong>{selected.restaurant.name}</strong>
            <small>
              {selected.restaurant.distance_km != null ? `${selected.restaurant.distance_km}km` : '距离待确认'}
              {selected.restaurant.address ? ` · ${selected.restaurant.address}` : ''}
            </small>
          </div>
          {navigationUrl && (
            <a href={navigationUrl} target="_blank" rel="noopener noreferrer">
              步行导航
            </a>
          )}
        </div>
      )}
    </div>
  )
}
