import { Routes } from '@angular/router';

import { GENERATED_ROUTES, PUBLIC_ROUTES } from './generated/routes';
import { authGuard } from './core/auth/auth.guard';
import { NotFoundComponent } from './pages/not-found/not-found.component';
import { ShellComponent } from './shell/shell.component';

export const routes: Routes = [
  ...PUBLIC_ROUTES,
  {
    path: '',
    component: ShellComponent,
    canActivateChild: [authGuard],
    children: [
      ...GENERATED_ROUTES,
      { path: '__kit', loadComponent: () => import('./pages/kit-showcase/kit-showcase.component').then((m) => m.KitShowcaseComponent) },
      { path: '**', component: NotFoundComponent },
    ],
  },
];
