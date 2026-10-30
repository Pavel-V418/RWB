import {
  AfterViewInit, ChangeDetectionStrategy, Component, ElementRef, NgZone, OnDestroy, effect, inject, input, output, signal, viewChild,
} from '@angular/core';
import maplibregl from 'maplibre-gl';

import { environment } from '../../../environments/environment';
import { Severity } from '../value/format';
import { MapFeature, MapLayerData } from './features';

export interface FeatureClick {
  layerId: string;
  id: string;
  properties: MapFeature['properties'];
}

const BASEMAP_SOURCE = 'osm';

/** Встроенный стиль: нейтральный фон + растровая подложка OSM. Без сети остаётся фон и слои данных. */
function defaultStyle(): maplibregl.StyleSpecification {
  return {
    version: 8,
    sources: {
      [BASEMAP_SOURCE]: {
        type: 'raster',
        tiles: ['https://tile.openstreetmap.org/{z}/{x}/{y}.png'],
        tileSize: 256,
        attribution: '© OpenStreetMap',
        maxzoom: 19,
      },
    },
    layers: [
      { id: 'sf-background', type: 'background', paint: { 'background-color': cssVar('--p-surface-100', 'whitesmoke') } },
      { id: BASEMAP_SOURCE, type: 'raster', source: BASEMAP_SOURCE },
    ],
  };
}

/** Читает цвет из CSS-токена (--sf-map-<severity>), чтобы в коде не было литералов цветов. */
function cssVar(name: string, fallback: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback;
}

function tokenColor(severity: Severity | 'default'): string {
  return cssVar(severity === 'default' ? '--p-primary-color' : `--sf-map-${severity}`, 'gray');
}

@Component({
  selector: 'app-map',
  template: `<div #container class="sf-map" [style.height]="height()" role="region" aria-label="Карта"></div>
    @if (basemapOffline()) {
      <div class="sf-map-note" data-testid="map-basemap-offline">Подложка карты недоступна — показаны только объекты</div>
    }`,
  styleUrl: './app-map.component.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class AppMapComponent implements AfterViewInit, OnDestroy {
  readonly layers = input<MapLayerData[]>([]);
  /** [lon, lat] из спеки экрана; без него — центр из окружения (APP_MAP_CENTER), иначе обзор всей территории. */
  readonly center = input<readonly number[] | null>(null);
  readonly zoom = input<number | null>(null);
  readonly fitToData = input(true);
  readonly height = input('100%');
  readonly featureClick = output<FeatureClick>();
  /** Тайлы подложки не загрузились (нет интернета на стенде) — показываем фон и объекты, без шума в консоли. */
  protected readonly basemapOffline = signal(false);

  private readonly container = viewChild.required<ElementRef<HTMLDivElement>>('container');
  private readonly zone = inject(NgZone);
  private map: maplibregl.Map | null = null;
  private loaded = false;
  private fitted = false;
  private markers: maplibregl.Marker[] = [];
  private known = new Set<string>();
  private resizeObserver: ResizeObserver | null = null;
  private popup: maplibregl.Popup | null = null;

  constructor() {
    effect(() => {
      const layers = this.layers();
      if (this.loaded) this.render(layers);
    });
  }

  ngAfterViewInit(): void {
    this.zone.runOutsideAngular(() => {
      this.map = new maplibregl.Map({
        container: this.container().nativeElement,
        style: environment.mapStyleUrl ?? defaultStyle(),
        center: (this.center() ?? environment.mapCenter) as [number, number],
        zoom: this.zoom() ?? environment.mapZoom,
        attributionControl: { compact: true },
      });
      this.map.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'top-right');
      this.map.on('load', () => {
        this.loaded = true;
        this.render(this.layers());
      });
      // Обработчик error отключает вывод maplibre в консоль: ошибки подложки гасим, ошибки данных пишем сами.
      this.map.on('error', (e) => this.onMapError(e as unknown as { sourceId?: string; error?: { message?: string } }));
      if (typeof ResizeObserver !== 'undefined') {
        this.resizeObserver = new ResizeObserver(() => this.map?.resize());
        this.resizeObserver.observe(this.container().nativeElement);
      }
    });
  }

  private render(layers: MapLayerData[]): void {
    const map = this.map;
    if (!map) return;
    this.markers.forEach((m) => m.remove());
    this.markers = [];
    for (const layer of layers) {
      // Сбой одного слоя (неожиданная геометрия от API) не должен гасить остальные слои карты.
      try {
        const data = { type: 'FeatureCollection', features: layer.features } as maplibregl.GeoJSONSourceSpecification['data'];
        const src = map.getSource(layer.id) as maplibregl.GeoJSONSource | undefined;
        if (src) {
          src.setData(data as Parameters<maplibregl.GeoJSONSource['setData']>[0]);
        } else {
          map.addSource(layer.id, { type: 'geojson', data });
          this.addStyleLayers(layer);
        }
        this.addLabels(layer);
      } catch (e) {
        console.warn(`app-map: слой ${layer.id} пропущен`, e);
      }
    }
    if (this.fitToData() && !this.fitted) this.fit(layers);
  }

  private addStyleLayers(layer: MapLayerData): void {
    const map = this.map!;
    const base = tokenColor(layer.severity ?? 'default');
    const color: maplibregl.ExpressionSpecification = [
      'match', ['coalesce', ['get', '__severity'], ''],
      'success', tokenColor('success'), 'info', tokenColor('info'), 'warn', tokenColor('warn'),
      'danger', tokenColor('danger'), 'secondary', tokenColor('secondary'), 'contrast', tokenColor('contrast'),
      base,
    ];
    const opacity = Number(cssVar('--sf-map-fill-opacity', '0.18'));
    const ids: string[] = [];
    if (layer.kind === 'polygon') {
      map.addLayer({ id: `${layer.id}-fill`, type: 'fill', source: layer.id, paint: { 'fill-color': color, 'fill-opacity': opacity } });
      map.addLayer({ id: `${layer.id}-outline`, type: 'line', source: layer.id, paint: { 'line-color': color, 'line-width': 2 } });
      ids.push(`${layer.id}-fill`);
    } else if (layer.kind === 'line') {
      map.addLayer({
        id: `${layer.id}-line`, type: 'line', source: layer.id,
        layout: { 'line-cap': 'round', 'line-join': 'round' },
        paint: { 'line-color': color, 'line-width': 4, 'line-opacity': 0.85 },
      });
      ids.push(`${layer.id}-line`);
    } else {
      map.addLayer({
        id: `${layer.id}-circle`, type: 'circle', source: layer.id,
        paint: { 'circle-radius': 7, 'circle-color': color, 'circle-stroke-width': 2, 'circle-stroke-color': cssVar('--p-surface-0', 'white') },
      });
      ids.push(`${layer.id}-circle`);
    }
    for (const id of ids) {
      map.on('click', id, (e) => {
        const f = e.features?.[0];
        if (!f) return;
        const props = f.properties as MapFeature['properties'];
        // maplibre сериализует вложенные объекты в строки
        const popupRows = typeof props.__popup === 'string' ? JSON.parse(props.__popup as string) : props.__popup;
        this.showPopup(e.lngLat, { ...props, __popup: popupRows }, layer);
      });
      map.on('mouseenter', id, () => (map.getCanvas().style.cursor = 'pointer'));
      map.on('mouseleave', id, () => (map.getCanvas().style.cursor = ''));
    }
    this.known.add(layer.id);
  }

  private showPopup(at: maplibregl.LngLat, props: MapFeature['properties'], layer: MapLayerData): void {
    const el = document.createElement('div');
    el.className = 'sf-map-popup';
    if (props.__title) {
      const t = document.createElement('div');
      t.className = 'sf-map-popup-title';
      t.textContent = props.__title;
      el.appendChild(t);
    }
    for (const row of props.__popup ?? []) {
      const r = document.createElement('div');
      r.className = 'sf-map-popup-row';
      const l = document.createElement('span');
      l.textContent = row.label;
      const v = document.createElement('strong');
      v.textContent = row.value;
      r.append(l, v);
      el.appendChild(r);
    }
    if (layer.clickable && props.__id) {
      // Кнопка, а не <a href="javascript:…">: такие адреса блокирует CSP (script-src 'self').
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'sf-map-popup-link';
      btn.textContent = 'Подробнее →';
      btn.setAttribute('data-testid', `map-${layer.id}-open`);
      btn.addEventListener('click', (ev: MouseEvent) => {
        ev.preventDefault();
        this.zone.run(() => this.featureClick.emit({ layerId: layer.id, id: String(props.__id), properties: props }));
      });
      el.appendChild(btn);
    }
    this.popup?.remove();
    this.popup = new maplibregl.Popup({ closeButton: true, maxWidth: '280px' }).setLngLat(at).setDOMContent(el).addTo(this.map!);
  }

  /** Подписи через HTML-маркеры: не требуют glyphs в стиле (офлайн-тайлы, растровый OSM). */
  private addLabels(layer: MapLayerData): void {
    for (const f of layer.features) {
      const label = f.properties.__label;
      if (!label) continue;
      const pos = this.labelPosition(f);
      if (!pos) continue;
      const el = document.createElement('div');
      el.className = `sf-map-label sf-map-label--${layer.kind}`;
      el.textContent = label;
      this.markers.push(new maplibregl.Marker({ element: el, anchor: layer.kind === 'point' ? 'top' : 'center', offset: layer.kind === 'point' ? [0, 8] : [0, 0] }).setLngLat(pos).addTo(this.map!));
    }
  }

  private labelPosition(f: MapFeature): [number, number] | null {
    const g = f.geometry;
    const c = g.coordinates as unknown[] | undefined;
    if (g.type === 'Point') return Array.isArray(c) && typeof c[0] === 'number' ? (c as [number, number]) : null;
    const ring = (g.type === 'Polygon' ? c?.[0] : g.type === 'MultiPolygon' ? (c?.[0] as unknown[] | undefined)?.[0] : null) as number[][] | null | undefined;
    if (!Array.isArray(ring) || !ring.length) return null;
    const [sx, sy] = ring.reduce(([x, y], [cx, cy]) => [x + cx, y + cy], [0, 0]);
    return [sx / ring.length, sy / ring.length];
  }

  private fit(layers: MapLayerData[]): void {
    const bounds = new maplibregl.LngLatBounds();
    const visit = (c: unknown): void => {
      if (Array.isArray(c) && typeof c[0] === 'number') bounds.extend(c as [number, number]);
      else if (Array.isArray(c)) c.forEach(visit);
    };
    layers.forEach((l) => l.features.forEach((f) => visit(f.geometry.coordinates)));
    if (!bounds.isEmpty()) {
      this.map!.fitBounds(bounds, { padding: 48, maxZoom: 16, duration: 0 });
      this.fitted = true;
    }
  }

  private onMapError(e: { sourceId?: string; error?: { message?: string } }): void {
    const fromData = !!e.sourceId && this.known.has(e.sourceId);
    if (fromData) {
      console.error(`app-map: ошибка слоя ${e.sourceId}`, e.error?.message ?? e);
      return;
    }
    // всё остальное — подложка (встроенная OSM или стиль из APP_MAP_STYLE_URL)
    if (!this.basemapOffline()) {
      if (!environment.mapStyleUrl && this.map?.getLayer(BASEMAP_SOURCE)) {
        this.map.setLayoutProperty(BASEMAP_SOURCE, 'visibility', 'none');
      }
      this.zone.run(() => this.basemapOffline.set(true));
      console.warn('app-map: подложка карты недоступна, показываются только объекты', e.error?.message ?? '');
    }
  }

  /** Переместить карту к точке (проигрывание трека). */
  flyTo(lngLat: [number, number]): void {
    this.map?.easeTo({ center: lngLat, duration: 200 });
  }

  ngOnDestroy(): void {
    this.resizeObserver?.disconnect();
    this.markers.forEach((m) => m.remove());
    this.map?.remove();
  }
}
