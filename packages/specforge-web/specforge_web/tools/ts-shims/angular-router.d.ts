import { Observable } from 'rxjs';
export interface ParamMap { keys: string[]; get(k: string): string | null; }
export declare class Router { url: string; events: Observable<unknown>; navigate(c: unknown[], e?: unknown): Promise<boolean>; navigateByUrl(u: string): Promise<boolean>; createUrlTree(c: unknown[], e?: unknown): unknown; }
export declare class ActivatedRoute { paramMap: Observable<ParamMap>; snapshot: { queryParamMap: ParamMap }; }
export declare class NavigationEnd {}
export type Routes = unknown[];
export type CanActivateChildFn = (route: unknown, state: { url: string }) => unknown;
export declare const RouterLink: any, RouterOutlet: any;
export declare function provideRouter(...a: unknown[]): unknown;
export declare function withComponentInputBinding(): unknown;
export declare function withInMemoryScrolling(o: unknown): unknown;
