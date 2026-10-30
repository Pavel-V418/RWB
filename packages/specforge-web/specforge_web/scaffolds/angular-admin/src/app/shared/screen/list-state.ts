import { computed, signal } from '@angular/core';
import { TableLazyLoadEvent } from 'primeng/table';

export interface ListOptions {
  pageSize: number;
  paged: boolean;
  sortParam?: string;
  defaultSort?: string;
  searchParam?: string;
}

/**
 * Состояние списка: страница, сортировка, поиск, фильтры, вкладки.
 * Любое изменение сбрасывает страницу на первую и вызывает reload (тихий — без скелетона).
 */
export class ListState {
  readonly page = signal(1);
  readonly sort = signal<string | undefined>(undefined);
  readonly search = signal('');
  readonly query = signal<Record<string, unknown>>({});
  readonly tab = signal(0);
  readonly first = computed(() => (this.page() - 1) * this.opts.pageSize);
  readonly hasFilters = computed(() => !!this.search() || Object.values(this.query()).some((v) => v !== undefined && v !== null && v !== ''));

  constructor(
    private readonly opts: ListOptions,
    private readonly reload: () => void,
  ) {
    this.sort.set(opts.defaultSort);
  }

  get size(): number {
    return this.opts.pageSize;
  }

  /** Параметры запроса без констант экрана и вкладок. */
  params(): Record<string, unknown> {
    const p: Record<string, unknown> = { ...this.query() };
    if (this.opts.paged) {
      p['page'] = this.page();
      p['size'] = this.opts.pageSize;
    }
    if (this.opts.sortParam && this.sort()) p[this.opts.sortParam] = this.sort();
    if (this.opts.searchParam && this.search()) p[this.opts.searchParam] = this.search();
    return p;
  }

  onLazyLoad(e: TableLazyLoadEvent): void {
    const rows = e.rows ?? this.opts.pageSize;
    const page = Math.floor((e.first ?? 0) / rows) + 1;
    const field = typeof e.sortField === 'string' ? e.sortField : undefined;
    const sort = field ? `${field},${(e.sortOrder ?? 1) > 0 ? 'asc' : 'desc'}` : this.opts.defaultSort;
    if (page === this.page() && sort === this.sort()) return;
    this.page.set(page);
    this.sort.set(sort);
    this.reload();
  }

  onPage(first: number): void {
    this.page.set(Math.floor(first / this.opts.pageSize) + 1);
    this.reload();
  }

  setFilter(param: string, value: unknown): void {
    this.query.update((q) => ({ ...q, [param]: value === null || value === '' ? undefined : value }));
    this.page.set(1);
    this.reload();
  }

  /** date-range: param = "from,to"; конец периода — конец дня. */
  setRange(params: string, value: Date[] | null | undefined): void {
    const [fromP, toP] = params.split(',');
    const [from, to] = value ?? [];
    const end = to ?? from;
    this.query.update((q) => ({
      ...q,
      [fromP]: from ? new Date(from.getFullYear(), from.getMonth(), from.getDate()).toISOString() : undefined,
      [toP]: end ? new Date(end.getFullYear(), end.getMonth(), end.getDate(), 23, 59, 59).toISOString() : undefined,
    }));
    this.page.set(1);
    this.reload();
  }

  setSearch(value: string): void {
    const v = (value ?? '').trim();
    if (v === this.search()) return;
    this.search.set(v);
    this.page.set(1);
    this.reload();
  }

  setTab(index: number | string | undefined): void {
    this.tab.set(Number(index ?? 0));
    this.page.set(1);
    this.reload();
  }

  clear(): void {
    this.query.set({});
    this.search.set('');
    this.page.set(1);
    this.reload();
  }
}
