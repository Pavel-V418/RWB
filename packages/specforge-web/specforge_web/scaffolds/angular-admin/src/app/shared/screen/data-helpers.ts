/** Элементы ответа: Page {items} | массив | null → массив. */
export function itemsOf<T>(v: { items: T[] } | T[] | null | undefined): T[] {
  if (!v) return [];
  return Array.isArray(v) ? v : (v.items ?? []);
}

export function totalOf(v: { total?: number; items?: unknown[] } | unknown[] | null | undefined): number {
  if (!v) return 0;
  if (Array.isArray(v)) return v.length;
  return v.total ?? v.items?.length ?? 0;
}

/** Объект-ответ (карточка) → массив из одного элемента (для слоёв карты). */
export function asList<T>(v: T | null | undefined): T[] {
  return v ? [v] : [];
}

/** realtime prepend: новый элемент в начало страницы/массива. */
export function prependToPage<P>(page: P, item: unknown, limit: number): P {
  if (Array.isArray(page)) return [item, ...page].slice(0, limit) as P;
  const p = page as { items?: unknown[]; total?: number };
  if (!Array.isArray(p.items)) return page;
  const exists = p.items.some((x) => (x as { id?: unknown })?.id === (item as { id?: unknown })?.id);
  if (exists) return page;
  return { ...p, items: [item, ...p.items].slice(0, limit), total: (p.total ?? p.items.length) + 1 } as P;
}

/** realtime patch: слить payload в элемент с id = subject.id. Возвращает ту же ссылку, если элемента нет. */
export function patchInPage<P>(page: P, id: string | undefined, patch: Record<string, unknown>): P {
  if (!id) return page;
  const apply = (arr: unknown[]) => {
    let hit = false;
    const next = arr.map((x) => {
      if ((x as { id?: unknown })?.id === id) {
        hit = true;
        return { ...(x as object), ...patch };
      }
      return x;
    });
    return hit ? next : arr;
  };
  if (Array.isArray(page)) {
    const next = apply(page);
    return (next === page ? page : next) as P;
  }
  const p = page as { items?: unknown[] };
  if (!Array.isArray(p.items)) {
    return ((page as { id?: unknown })?.id === id ? { ...(page as object), ...patch } : page) as P;
  }
  const items = apply(p.items);
  return (items === p.items ? page : { ...p, items }) as P;
}
