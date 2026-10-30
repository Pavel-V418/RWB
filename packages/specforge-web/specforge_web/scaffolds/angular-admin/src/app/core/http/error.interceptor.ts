import { HttpErrorResponse, HttpInterceptorFn } from '@angular/common/http';
import { inject } from '@angular/core';
import { MessageService } from 'primeng/api';
import { catchError, throwError } from 'rxjs';

import { SILENT_ERRORS, problemMessage, toProblem } from './problem';

/**
 * Ошибки GET отображает состояние экрана (app-state), поэтому toast — только для
 * изменяющих запросов и только если запрос не помечен SILENT_ERRORS.
 */
export const errorInterceptor: HttpInterceptorFn = (req, next) => {
  const messages = inject(MessageService);
  return next(req).pipe(
    catchError((err: unknown) => {
      const silent = req.context.get(SILENT_ERRORS) || req.method === 'GET';
      if (err instanceof HttpErrorResponse && err.status !== 401 && !silent) {
        const p = toProblem(err);
        messages.add({ severity: 'error', summary: 'Ошибка', detail: problemMessage(p), life: 6000 });
      }
      return throwError(() => err);
    }),
  );
};
