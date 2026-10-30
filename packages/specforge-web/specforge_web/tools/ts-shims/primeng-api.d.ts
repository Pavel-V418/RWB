export interface MenuItem { label?: string; icon?: string; routerLink?: unknown; id?: string; command?: (e?: unknown) => void; [k: string]: unknown; }
export declare class MessageService { add(m: { severity?: string; summary?: string; detail?: string; life?: number }): void; clear(): void; }
export declare class ConfirmationService { confirm(o: Record<string, unknown>): this; }
export type Translation = Record<string, unknown>;
