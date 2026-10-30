import { DestroyRef, computed, inject, signal } from '@angular/core';

/** Проигрывание трека по шкале времени (map.playback в screen-spec). */
export class PlaybackController<T> {
  readonly index = signal(0);
  readonly playing = signal(false);
  readonly speed = signal(1);
  private readonly points = signal<T[]>([]);
  private timer: ReturnType<typeof setInterval> | null = null;

  readonly length = computed(() => this.points().length);
  readonly current = computed<T | null>(() => this.points()[this.index()] ?? null);

  constructor(destroyRef: DestroyRef) {
    destroyRef.onDestroy(() => this.pause());
  }

  setPoints(points: T[]): void {
    this.pause();
    this.points.set(points);
    this.index.set(0);
  }

  toggle(): void {
    if (this.playing()) this.pause();
    else this.play();
  }

  play(): void {
    if (this.length() < 2) return;
    if (this.index() >= this.length() - 1) this.index.set(0);
    this.playing.set(true);
    this.timer = setInterval(() => {
      const next = this.index() + this.speed();
      if (next >= this.length() - 1) {
        this.index.set(this.length() - 1);
        this.pause();
      } else {
        this.index.set(next);
      }
    }, 250);
  }

  pause(): void {
    this.playing.set(false);
    if (this.timer) clearInterval(this.timer);
    this.timer = null;
  }

  seek(i: number): void {
    this.index.set(Math.max(0, Math.min(i, this.length() - 1)));
  }
}

export function playback<T>(): PlaybackController<T> {
  return new PlaybackController<T>(inject(DestroyRef));
}
