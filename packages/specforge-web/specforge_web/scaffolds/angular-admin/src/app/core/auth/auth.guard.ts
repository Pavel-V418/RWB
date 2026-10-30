import { inject } from '@angular/core';
import { CanActivateChildFn, Router } from '@angular/router';

import { AuthService } from './auth.service';

/**
 * Пускает только аутентифицированных. Роли проверяет сам экран (состояние «нет доступа»),
 * чтобы пользователь видел понятное объяснение, а не молчаливый редирект.
 */
export const authGuard: CanActivateChildFn = (_route, state) => {
  const auth = inject(AuthService);
  if (auth.isAuthenticated()) return true;
  return inject(Router).createUrlTree(['/login'], { queryParams: { returnUrl: state.url } });
};
