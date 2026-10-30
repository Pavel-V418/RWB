import { hasPositions, toFeatures } from './features';

describe('features', () => {
  it('hasPositions: точка, полигон, пустые и битые координаты', () => {
    expect(hasPositions([1, 2])).toBe(true);
    expect(hasPositions([[[1, 2], [3, 4], [5, 6], [1, 2]]])).toBe(true);
    expect(hasPositions([])).toBe(false);
    expect(hasPositions([[]])).toBe(false);
    expect(hasPositions([[[]]])).toBe(false);
    expect(hasPositions(null)).toBe(false);
    expect(hasPositions(['a', 'b'])).toBe(false);
  });

  it('toFeatures пропускает объекты с пустой геометрией', () => {
    const items = [
      { id: 'a', geo: { type: 'Polygon', coordinates: [[[0, 0], [1, 0], [1, 1], [0, 0]]] } },
      { id: 'b', geo: { type: 'MultiPolygon', coordinates: [] } },
      { id: 'c', geo: { type: 'Polygon', coordinates: [] } },
      { id: 'd' },
    ];
    const f = toFeatures(items, { geometryField: 'geo' });
    expect(f.map((x) => x.properties.__id)).toEqual(['a']);
  });
});
