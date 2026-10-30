import { Severity, StatusMap, ValueFormat, formatValue, getPath, isBlank } from '../value/format';

export type GeoKind = 'polygon' | 'point' | 'line';

export interface MapFeature {
  type: 'Feature';
  id?: string | number;
  geometry: { type: string; coordinates: unknown };
  properties: {
    __id?: string;
    __label?: string;
    __severity?: Severity;
    __popup?: { label: string; value: string }[];
    __title?: string;
    [k: string]: unknown;
  };
}

export interface MapLayerData {
  id: string;
  kind: GeoKind;
  features: MapFeature[];
  /** Клик по объекту → featureClick (переход на экран linkTo) */
  clickable?: boolean;
  /** Цвет слоя по умолчанию */
  severity?: Severity;
}

export interface FeatureOptions {
  geometryField: string;
  labelField?: string;
  colorBy?: string;
  statusMap?: StatusMap;
  popupFields?: { field: string; label: string; format?: ValueFormat; statusMap?: StatusMap }[];
  idField?: string;
}

/** Есть ли в координатах хотя бы одна пара чисел (пустые полигоны и мусор отбрасываем). */
export function hasPositions(c: unknown): boolean {
  if (!Array.isArray(c) || c.length === 0) return false;
  if (typeof c[0] === 'number') return c.length >= 2 && Number.isFinite(c[0]) && Number.isFinite(c[1] as number);
  return c.some(hasPositions);
}

/** Элементы API → GeoJSON-фичи для app-map. Элементы без геометрии или с пустой геометрией пропускаются. */
export function toFeatures(items: readonly unknown[] | null | undefined, o: FeatureOptions): MapFeature[] {
  if (!items) return [];
  const out: MapFeature[] = [];
  for (const item of items) {
    const geometry = getPath(item, o.geometryField) as MapFeature['geometry'] | undefined;
    if (!geometry || typeof geometry.type !== 'string' || isBlank(geometry.coordinates) || !hasPositions(geometry.coordinates)) continue;
    const id = String(getPath(item, o.idField ?? 'id') ?? out.length);
    const colorValue = o.colorBy ? String(getPath(item, o.colorBy)) : undefined;
    out.push({
      type: 'Feature',
      id: out.length,
      geometry,
      properties: {
        __id: id,
        __label: o.labelField ? formatValue(getPath(item, o.labelField)) : undefined,
        __severity: colorValue ? o.statusMap?.[colorValue]?.severity : undefined,
        __title: o.labelField ? formatValue(getPath(item, o.labelField)) : undefined,
        __popup: (o.popupFields ?? [])
          .map((f) => ({ label: f.label, value: formatValue(getPath(item, f.field), f.format ?? 'text', f.statusMap) }))
          .filter((r) => r.value !== ''),
      },
    });
  }
  return out;
}

/** Трек из точек → одна линия (для слоёв kind=line из списка точек). */
export function pointsToLine(features: MapFeature[]): MapFeature[] {
  const coords = features.filter((f) => f.geometry.type === 'Point').map((f) => f.geometry.coordinates);
  if (coords.length < 2) return features.filter((f) => f.geometry.type !== 'Point');
  return [{ type: 'Feature', geometry: { type: 'LineString', coordinates: coords }, properties: {} }];
}
