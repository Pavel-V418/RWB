import { ChangeDetectionStrategy, Component, DestroyRef, computed, inject, signal } from '@angular/core';
import { NavigationEnd, Router, RouterOutlet } from '@angular/router';
import { MenuItem } from 'primeng/api';
import { AvatarModule } from 'primeng/avatar';
import { ButtonModule } from 'primeng/button';
import { ConfirmDialogModule } from 'primeng/confirmdialog';
import { DrawerModule } from 'primeng/drawer';
import { MenuModule } from 'primeng/menu';
import { TagModule } from 'primeng/tag';
import { ToastModule } from 'primeng/toast';
import { filter } from 'rxjs';

import { AuthService } from '../core/auth/auth.service';
import { RealtimeService } from '../core/realtime/realtime.service';
import { APP_META } from '../generated/app-meta';
import { NAVIGATION } from '../generated/navigation';

const MOBILE_QUERY = '(max-width: 767.98px)';

@Component({
  selector: 'app-shell',
  imports: [RouterOutlet, AvatarModule, ButtonModule, ConfirmDialogModule, DrawerModule, MenuModule, TagModule, ToastModule],
  templateUrl: './shell.component.html',
  styleUrl: './shell.component.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class ShellComponent {
  protected readonly auth = inject(AuthService);
  protected readonly realtime = inject(RealtimeService);
  protected readonly title = APP_META.title;
  protected readonly isMobile = signal(window.matchMedia(MOBILE_QUERY).matches);
  protected readonly drawerOpen = signal(false);

  protected readonly menu = computed<MenuItem[]>(() =>
    NAVIGATION.filter((n) => this.auth.hasAnyRole(n.roles)).map((n) => ({
      label: n.label,
      icon: n.icon,
      routerLink: n.path,
      routerLinkActiveOptions: { exact: n.path === '/' },
      id: `nav-${n.screen}`,
      command: () => this.drawerOpen.set(false),
    })),
  );

  protected readonly userMenu: MenuItem[] = [
    { label: 'Выйти', icon: 'pi pi-sign-out', id: 'shell-logout', command: () => this.auth.logout() },
  ];

  protected readonly initials = computed(() =>
    (this.auth.user()?.name ?? '?').split(/\s+/).map((p) => p[0]).slice(0, 2).join('').toUpperCase(),
  );

  protected readonly realtimeLabel = computed(() =>
    ({ online: 'Онлайн', connecting: 'Подключение…', offline: 'Нет связи', disabled: '' })[this.realtime.status()],
  );
  protected readonly realtimeSeverity = computed(() =>
    ({ online: 'success', connecting: 'warn', offline: 'danger', disabled: 'secondary' } as const)[this.realtime.status()],
  );

  constructor() {
    const mq = window.matchMedia(MOBILE_QUERY);
    const onChange = (e: MediaQueryListEvent) => this.isMobile.set(e.matches);
    mq.addEventListener('change', onChange);
    inject(DestroyRef).onDestroy(() => mq.removeEventListener('change', onChange));
    inject(Router).events.pipe(filter((e) => e instanceof NavigationEnd)).subscribe(() => this.drawerOpen.set(false));
  }
}
