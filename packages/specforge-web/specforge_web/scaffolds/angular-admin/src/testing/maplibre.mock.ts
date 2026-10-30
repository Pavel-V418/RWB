// Заглушка maplibre-gl для jsdom (нет WebGL). Реальная карта проверяется в Playwright.
class Evented {
  on() { return this; }
  off() { return this; }
  once(_e: string, cb?: () => void) { cb?.(); return this; }
}
export class Map extends Evented {
  constructor(_opts: unknown) { super(); }
  addSource() { return this; }
  getSource() { return { setData: () => undefined }; }
  addLayer() { return this; }
  setLayoutProperty() { return this; }
  getLayer() { return undefined; }
  removeLayer() { return this; }
  removeSource() { return this; }
  fitBounds() { return this; }
  getCanvas() { return { style: {} }; }
  isStyleLoaded() { return true; }
  resize() { return this; }
  addControl() { return this; }
  easeTo() { return this; }
  remove() { return undefined; }
}
export class Popup extends Evented {
  setLngLat() { return this; }
  setDOMContent() { return this; }
  addTo() { return this; }
  remove() { return this; }
}
export class Marker {
  constructor(_o?: unknown) {}
  setLngLat() { return this; }
  addTo() { return this; }
  remove() { return this; }
}
export class LngLatBounds {
  extend() { return this; }
  isEmpty() { return true; }
}
export class NavigationControl {}
export default { Map, Popup, Marker, LngLatBounds, NavigationControl };
