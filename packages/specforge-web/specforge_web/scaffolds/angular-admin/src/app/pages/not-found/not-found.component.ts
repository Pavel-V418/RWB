import { ChangeDetectionStrategy, Component } from '@angular/core';
import { RouterLink } from '@angular/router';
import { ButtonModule } from 'primeng/button';
import { CardModule } from 'primeng/card';

@Component({
  selector: 'app-not-found',
  imports: [ButtonModule, CardModule, RouterLink],
  template: `
    <div class="sf-page">
      <p-card data-testid="not-found">
        <div class="sf-stack" style="align-items: center; text-align: center">
          <i class="pi pi-map-marker sf-muted" style="font-size: 2.5rem" aria-hidden="true"></i>
          <h1 style="margin: 0">Страница не найдена</h1>
          <p class="sf-muted" style="margin: 0">Проверьте адрес или вернитесь на главную</p>
          <p-button label="На главную" icon="pi pi-home" routerLink="/" data-testid="not-found-home" />
        </div>
      </p-card>
    </div>
  `,
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class NotFoundComponent {}
