// Слот реализован вручную (консоль конвейера). Запуск конвейера на бизнес-требованиях.
// Требования: FR-01
import { ChangeDetectionStrategy, Component, computed, inject, input, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Router } from '@angular/router';
import { MessageService } from 'primeng/api';
import { ButtonModule } from 'primeng/button';
import { CardModule } from 'primeng/card';
import { FileUploadModule } from 'primeng/fileupload';
import { InputTextModule } from 'primeng/inputtext';
import { MessageModule } from 'primeng/message';
import { SelectButtonModule } from 'primeng/selectbutton';
import { TextareaModule } from 'primeng/textarea';

import { newIdempotencyKey } from '../core/http/idempotency';
import { problemMessage, toProblem } from '../core/http/problem';
import { ApiService } from '../generated/api/api.service';

const MAX_FILE = 10 * 1024 * 1024;

@Component({
  selector: 'slot-run-new-start-form',
  imports: [FormsModule, ButtonModule, CardModule, FileUploadModule, InputTextModule, MessageModule, SelectButtonModule, TextareaModule],
  templateUrl: './run-new-start-form.slot.html',
  styleUrl: './run-new-start-form.slot.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class RunNewStartFormSlot {
  readonly data = input<Record<string, unknown>>({});
  private readonly api = inject(ApiService);
  private readonly router = inject(Router);
  private readonly messages = inject(MessageService);

  protected readonly modes = [
    { label: 'Autopilot', value: 'autopilot' },
    { label: 'С подтверждением', value: 'approve' },
  ];
  protected readonly title = signal('');
  protected readonly text = signal('');
  protected readonly file = signal<File | null>(null);
  protected readonly mode = signal<'autopilot' | 'approve'>('autopilot');
  protected readonly saving = signal(false);
  protected readonly error = signal<string | null>(null);
  protected readonly touched = signal(false);
  protected readonly valid = computed(() => !!this.file() || this.text().trim().length >= 20);
  private idempotencyKey = newIdempotencyKey();

  protected onFile(files: File[] | undefined): void {
    const f = files?.[0] ?? null;
    if (f && f.size > MAX_FILE) {
      this.error.set('Файл больше 10 МБ');
      return;
    }
    this.error.set(null);
    this.file.set(f);
    if (f && !this.title()) this.title.set(f.name.replace(/\.[^.]+$/, ''));
  }

  protected submit(): void {
    this.touched.set(true);
    if (!this.valid()) return;
    const fd = new FormData();
    fd.append('mode', this.mode());
    if (this.title().trim()) fd.append('title', this.title().trim());
    const f = this.file();
    if (f) fd.append('file', f, f.name);
    else fd.append('text', this.text().trim());
    this.saving.set(true);
    this.error.set(null);
    this.api.createRun(fd, {}, { idempotencyKey: this.idempotencyKey, silent: true }).subscribe({
      next: (run) => {
        this.saving.set(false);
        this.idempotencyKey = newIdempotencyKey();
        this.messages.add({ severity: 'success', summary: 'Конвейер запущен', life: 4000 });
        void this.router.navigate(['/runs', run.id]);
      },
      error: (e: unknown) => {
        this.saving.set(false);
        this.error.set(problemMessage(toProblem(e)));
      },
    });
  }
}
