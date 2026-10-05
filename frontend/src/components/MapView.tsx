/**
 * Leaflet map.
 *
 * Only points with real coordinates are plotted. An incident whose location could
 * not be resolved is deliberately *not* dropped silently - it is listed in the
 * side panel as "no verified position", because a marker that is missing and a
 * marker that is absent because the event is minor are not the same thing.
 */
import { useEffect } from 'react'
import { MapContainer, Marker, Popup, TileLayer } from 'react-leaflet'
import L from 'leaflet'
import type { FactStatus } from '../types'
import { ProvenanceBadge } from './Badges'

// Leaflet's default marker images break under bundlers; use a plain divIcon.
const pin = (colour: string) =>
  L.divIcon({
    className: '',
    html: `<div style="width:12px;height:12px;border-radius:9999px;background:${colour};border:2px solid #0b1220;box-shadow:0 0 0 1px ${colour}"></div>`,
    iconSize: [12, 12],
    iconAnchor: [6, 6],
  })

export type MapPoint = {
  id: string
  label: string
  latitude: number
  longitude: number
  factStatus?: FactStatus
  detail?: string
  kind?: 'incident' | 'resource' | 'shelter' | 'road'
}

const COLOURS: Record<string, string> = {
  incident: '#fb923c',
  resource: '#38bdf8',
  shelter: '#34d399',
  road: '#fbbf24',
}

function Fit({ points }: { points: MapPoint[] }) {
  const map = (window as unknown as { __map?: L.Map }).__map
  useEffect(() => {
    if (!map || points.length === 0) return
    const bounds = L.latLngBounds(points.map((p) => [p.latitude, p.longitude]))
    map.fitBounds(bounds, { padding: [40, 40], maxZoom: 13 })
  }, [map, points])
  return null
}

export function MapView({ points, height = 420 }: { points: MapPoint[]; height?: number }) {
  const located = points.filter((p) => Number.isFinite(p.latitude) && Number.isFinite(p.longitude))
  const unlocated = points.filter((p) => !located.includes(p))

  const centre: [number, number] = located.length
    ? [
        located.reduce((a, p) => a + p.latitude, 0) / located.length,
        located.reduce((a, p) => a + p.longitude, 0) / located.length,
      ]
    : [20, 0]

  return (
    <div>
      <MapContainer
        center={centre}
        zoom={located.length ? 11 : 2}
        style={{ height, width: '100%' }}
        ref={(m) => {
          if (m) (window as unknown as { __map?: L.Map }).__map = m
        }}
      >
        <TileLayer
          attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'
          url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
        />
        {located.map((p) => (
          <Marker
            key={p.id}
            position={[p.latitude, p.longitude]}
            icon={pin(COLOURS[p.kind ?? 'incident'] ?? COLOURS.incident)}
          >
            <Popup>
              <div className="text-xs">
                <div className="font-semibold">{p.label}</div>
                {p.factStatus && (
                  <div className="mt-1">
                    <ProvenanceBadge status={p.factStatus} />
                  </div>
                )}
                {p.detail && <div className="mt-1 text-slate-600">{p.detail}</div>}
              </div>
            </Popup>
          </Marker>
        ))}
        <Fit points={located} />
      </MapContainer>

      {unlocated.length > 0 && (
        <div className="mt-3 rounded-md border border-slate-800 bg-slate-900/60 p-3">
          <div className="text-xs font-semibold text-slate-300">
            {unlocated.length} item(s) with no verified position
          </div>
          <p className="mt-1 text-xs text-slate-500">
            These are absent from the map because their location could not be resolved. This is
            missing data, not a low priority.
          </p>
          <ul className="mt-2 space-y-1 text-xs text-slate-400">
            {unlocated.map((p) => (
              <li key={p.id}>
                {p.id} — {p.label}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}