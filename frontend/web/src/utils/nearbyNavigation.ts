import type { NearbyRestaurant } from '../types'

const AMAP_NAVIGATION_URL = 'https://uri.amap.com/navigation'
const NEARBY_NAVIGATION_MODE = 'walk'
const PI = Math.PI
const EARTH_AXIS = 6378245.0
const ECCENTRICITY = 0.00669342162296594323

const hasValidCoordinate = (value: number | undefined, min: number, max: number) =>
  typeof value === 'number' && Number.isFinite(value) && value >= min && value <= max

const hasValidOrigin = (value: string) => {
  const parts = value.split(',').map((part) => Number(part.trim()))
  return parts.length === 2 && hasValidCoordinate(parts[0], -180, 180) && hasValidCoordinate(parts[1], -90, 90)
}

const outOfChina = (lng: number, lat: number) =>
  lng < 72.004 || lng > 137.8347 || lat < 0.8293 || lat > 55.8271

const transformLat = (lng: number, lat: number) => {
  let ret = -100 + 2 * lng + 3 * lat + 0.2 * lat * lat + 0.1 * lng * lat + 0.2 * Math.sqrt(Math.abs(lng))
  ret += (20 * Math.sin(6 * lng * PI) + 20 * Math.sin(2 * lng * PI)) * 2 / 3
  ret += (20 * Math.sin(lat * PI) + 40 * Math.sin(lat / 3 * PI)) * 2 / 3
  ret += (160 * Math.sin(lat / 12 * PI) + 320 * Math.sin(lat * PI / 30)) * 2 / 3
  return ret
}

const transformLng = (lng: number, lat: number) => {
  let ret = 300 + lng + 2 * lat + 0.1 * lng * lng + 0.1 * lng * lat + 0.1 * Math.sqrt(Math.abs(lng))
  ret += (20 * Math.sin(6 * lng * PI) + 20 * Math.sin(2 * lng * PI)) * 2 / 3
  ret += (20 * Math.sin(lng * PI) + 40 * Math.sin(lng / 3 * PI)) * 2 / 3
  ret += (150 * Math.sin(lng / 12 * PI) + 300 * Math.sin(lng / 30 * PI)) * 2 / 3
  return ret
}

/** 浏览器 GPS 是 WGS-84，高德接口使用 GCJ-02；导航起点和附近搜索必须统一坐标系。 */
export function wgs84ToGcj02(value: string): string {
  const [lng, lat] = value.split(',').map((part) => Number(part.trim()))
  if (!hasValidCoordinate(lng, -180, 180) || !hasValidCoordinate(lat, -90, 90) || outOfChina(lng, lat)) {
    return value
  }

  const dLat = transformLat(lng - 105, lat - 35)
  const dLng = transformLng(lng - 105, lat - 35)
  const radLat = lat / 180 * PI
  let magic = Math.sin(radLat)
  magic = 1 - ECCENTRICITY * magic * magic
  const sqrtMagic = Math.sqrt(magic)
  const adjustedLat = (dLat * 180) / ((EARTH_AXIS * (1 - ECCENTRICITY)) / (magic * sqrtMagic) * PI)
  const adjustedLng = (dLng * 180) / (EARTH_AXIS / sqrtMagic * Math.cos(radLat) * PI)
  return `${(lng + adjustedLng).toFixed(6)},${(lat + adjustedLat).toFixed(6)}`
}

export const isMobileDevice = (userAgent = typeof navigator === 'undefined' ? '' : navigator.userAgent) =>
  /Android|iPhone|iPad|iPod/i.test(userAgent)

export function buildAmapNavigationUrl(
  restaurant: NearbyRestaurant,
  origin = '',
  userAgent?: string,
): string | null {
  if (
    !hasValidCoordinate(restaurant.lng, -180, 180) ||
    !hasValidCoordinate(restaurant.lat, -90, 90)
  ) {
    return null
  }

  const params = new URLSearchParams({
    to: `${restaurant.lng},${restaurant.lat},${restaurant.name}`,
    // Nearby restaurants are primarily reached on foot. Explicitly set the
    // mode because Amap otherwise falls back to driving navigation.
    mode: NEARBY_NAVIGATION_MODE,
    src: 'xiaoshan',
    callnative: '1',
  })

  const amapOrigin = wgs84ToGcj02(origin)
  if (!isMobileDevice(userAgent) && hasValidOrigin(amapOrigin)) {
    params.set('from', `${amapOrigin},我的位置`)
  }

  return `${AMAP_NAVIGATION_URL}?${params.toString()}`
}
