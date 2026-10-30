import { HttpClient } from '@angular/common/http';
import { Injectable, computed, inject, signal } from '@angular/core';
import { Router } from '@angular/router';
import { Observable, tap } from 'rxjs';

import { environment } from '../../../environments/environment';
import { APP_META } from '../../generated/app-meta';

/** Соглашение contracts/api/api-conventions.md §5 — одинаково для любого кейса. */
export interface CurrentUser {
  id: string;
  name: string;
  roles: string[];
  [key: string]: unknown; // напр. ownerId — доступно экранам как context:<key>
}

interface LoginResponse {
  accessToken: string;
  expiresIn: number;
  user: CurrentUser;
}

interface StoredSession {
  token: string;
  expiresAt: number;
  user: CurrentUser;
}

const STORAGE_KEY = 'sf.session';

function readSession(): StoredSession | null {
  try {
    const raw = sessionStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const s = JSON.parse(raw) as StoredSession;
    return s.expiresAt > Date.now() ? s : null;
  } catch {
    return null;
  }
}

function writeSession(s: StoredSession | null): void {
  try {
    if (s) sessionStorage.setItem(STORAGE_KEY, JSON.stringify(s));
    else sessionStorage.removeItem(STORAGE_KEY);
  } catch {
    /* приватный режим / запрет storage — сессия живёт только в памяти */
  }
}

@Injectable({ providedIn: 'root' })
export class AuthService {
  private readonly http = inject(HttpClient);
  private readonly router = inject(Router);
  private readonly session = signal<StoredSession | null>(readSession());

  readonly user = computed(() => this.session()?.user ?? null);
  readonly token = computed(() => this.session()?.token ?? null);
  readonly isAuthenticated = computed(() => !APP_META.authRequired || this.session() !== null);

  login(login: string, password: string): Observable<LoginResponse> {
    return this.http.post<LoginResponse>(`${environment.apiBaseUrl}/auth/login`, { login, password }).pipe(
      tap((r) => {
        const s: StoredSession = { token: r.accessToken, expiresAt: Date.now() + r.expiresIn * 1000, user: r.user };
        this.session.set(s);
        writeSession(s);
      }),
    );
  }

  logout(returnUrl?: string): void {
    this.session.set(null);
    writeSession(null);
    void this.router.navigate(['/login'], { queryParams: returnUrl ? { returnUrl } : {} });
  }

  hasAnyRole(roles: readonly string[] | undefined): boolean {
    if (!APP_META.authRequired) return true; // приложение без входа (например, локальная консоль)
    if (!roles || roles.length === 0) return true;
    const mine = this.user()?.roles ?? [];
    return roles.some((r) => mine.includes(r));
  }

  /** Значение для binding context:<key> (например ownerId текущего пользователя). */
  context(key: string): unknown {
    return this.user()?.[key];
  }
}
