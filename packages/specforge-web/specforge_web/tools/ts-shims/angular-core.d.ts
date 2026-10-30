// Минимальные типы @angular/core для быстрой проверки (tools/ts_check.py). НЕ для сборки.
export interface Signal<T> { (): T; }
export interface WritableSignal<T> extends Signal<T> { set(v: T): void; update(fn: (v: T) => T): void; asReadonly(): Signal<T>; }
export declare function signal<T>(v: T): WritableSignal<T>;
export declare function computed<T>(fn: () => T): Signal<T>;
export declare function effect(fn: () => void): { destroy(): void };
export declare function inject<T>(token: abstract new (...a: any[]) => T): T;
export interface InputSignal<T> extends Signal<T> {}
export declare const input: { <T>(initial: T, opts?: unknown): InputSignal<T>; <T>(): InputSignal<T | undefined>; required<T>(opts?: unknown): InputSignal<T>; };
export interface OutputEmitterRef<T> { emit(v: T): void; }
export declare function output<T = void>(): OutputEmitterRef<T>;
export declare const viewChild: { required<T>(locator: unknown): Signal<T>; <T>(locator: unknown): Signal<T | undefined>; };
export declare function Component(meta: unknown): ClassDecorator;
export declare function Directive(meta?: unknown): ClassDecorator;
export declare function Injectable(meta?: unknown): ClassDecorator;
export declare enum ChangeDetectionStrategy { OnPush = 0, Default = 1 }
export declare class DestroyRef { onDestroy(fn: () => void): () => void; }
export declare class NgZone { run<T>(fn: () => T): T; runOutsideAngular<T>(fn: () => T): T; }
export declare class ElementRef<T = any> { nativeElement: T; }
export interface OnInit { ngOnInit(): void; }
export interface OnDestroy { ngOnDestroy(): void; }
export interface AfterViewInit { ngAfterViewInit(): void; }
export type ApplicationConfig = { providers: unknown[] };
export type EnvironmentProviders = unknown;
export declare function makeEnvironmentProviders(p: unknown[]): EnvironmentProviders;
export declare const LOCALE_ID: unknown;
export declare function provideZoneChangeDetection(o?: unknown): unknown;
export declare function provideBrowserGlobalErrorListeners(): unknown;
