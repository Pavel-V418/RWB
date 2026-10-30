import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';
import { TagModule } from 'primeng/tag';

import { StatusMap, ValueFormat, formatValue, isBlank } from './format';

/** Отображение значения поля по формату из screen-spec (displayField.format). */
@Component({
  selector: 'app-value',
  imports: [TagModule],
  template: `
    @if (blank()) {
      <span class="sf-muted">{{ emptyText() }}</span>
    } @else if (format() === 'status') {
      <p-tag [value]="statusLabel()" [severity]="statusSeverity()" />
    } @else {
      <span [class.sf-mono]="format() === 'coordinates'" [attr.title]="format() === 'relative-time' ? absolute() : null">{{ text() }}</span>
    }
  `,
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class AppValueComponent {
  readonly value = input<unknown>();
  readonly format = input<ValueFormat>('text');
  readonly statusMap = input<StatusMap | undefined>(undefined);
  readonly emptyText = input('—');

  readonly blank = computed(() => isBlank(this.value()));
  readonly text = computed(() => formatValue(this.value(), this.format(), this.statusMap()));
  readonly absolute = computed(() => formatValue(this.value(), 'datetime'));
  readonly statusLabel = computed(() => this.statusMap()?.[String(this.value())]?.label ?? String(this.value()));
  readonly statusSeverity = computed(() => this.statusMap()?.[String(this.value())]?.severity ?? 'secondary');
}
