import { Observable } from 'rxjs';
export declare class HttpParams { set(k: string, v: string): HttpParams; append(k: string, v: string): HttpParams; }
export declare class HttpHeaders { constructor(h?: Record<string, string>); set(k: string, v: string): HttpHeaders; }
export declare class HttpContextToken<T> { constructor(def: () => T); }
export declare class HttpContext { set<T>(t: HttpContextToken<T>, v: T): HttpContext; get<T>(t: HttpContextToken<T>): T; }
export declare class HttpErrorResponse { status: number; statusText: string; error: any; }
type Opts = { params?: HttpParams; headers?: HttpHeaders; context?: HttpContext };
type TextOpts = Opts & { responseType: 'text' };
type BlobOpts = Opts & { responseType: 'blob' };
export declare class HttpClient {
  get(url: string, o: TextOpts): Observable<string>;
  get(url: string, o: BlobOpts): Observable<Blob>;
  get<T>(url: string, o?: Opts): Observable<T>;
  delete(url: string, o: TextOpts): Observable<string>;
  delete(url: string, o: BlobOpts): Observable<Blob>;
  delete<T>(url: string, o?: Opts): Observable<T>;
  post(url: string, body: unknown, o: TextOpts): Observable<string>;
  post(url: string, body: unknown, o: BlobOpts): Observable<Blob>;
  post<T>(url: string, body: unknown, o?: Opts): Observable<T>;
  put(url: string, body: unknown, o: TextOpts): Observable<string>;
  put(url: string, body: unknown, o: BlobOpts): Observable<Blob>;
  put<T>(url: string, body: unknown, o?: Opts): Observable<T>;
  patch(url: string, body: unknown, o: TextOpts): Observable<string>;
  patch(url: string, body: unknown, o: BlobOpts): Observable<Blob>;
  patch<T>(url: string, body: unknown, o?: Opts): Observable<T>;
}
export type HttpInterceptorFn = (req: any, next: (r: any) => Observable<any>) => Observable<any>;
export declare function provideHttpClient(...a: unknown[]): unknown;
export declare function withInterceptors(a: unknown[]): unknown;
