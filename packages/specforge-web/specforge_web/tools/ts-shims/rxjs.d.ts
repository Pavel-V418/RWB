export interface Subscription { unsubscribe(): void; }
export interface Observer<T> { next?: (v: T) => void; error?: (e: unknown) => void; complete?: () => void; }
export declare class Observable<T> {
  constructor(sub?: (s: { next(v: T): void; error(e: unknown): void; complete(): void }) => (() => void) | void);
  subscribe(o?: Observer<T> | ((v: T) => void)): Subscription;
  pipe(...ops: unknown[]): Observable<any>;
}
export declare class Subject<T> extends Observable<T> { next(v: T): void; asObservable(): Observable<T>; }
export declare const EMPTY: Observable<never>;
export declare function of<T>(...v: T[]): Observable<T>;
export declare function from<T>(v: T[] | Promise<T>): Observable<T>;
export declare function throwError(f: () => unknown): Observable<never>;
export declare const filter: any, map: any, tap: any, catchError: any, concatMap: any, delay: any, share: any, finalize: any, debounceTime: any;
