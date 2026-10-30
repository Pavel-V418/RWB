export declare class AbstractControl { errors: Record<string, any> | null; invalid: boolean; touched: boolean; dirty: boolean; setErrors(e: unknown): void; markAsTouched(): void; }
export declare class FormControl<T = any> extends AbstractControl { constructor(v: T, opts?: unknown); value: T; }
type ValueOf<C> = { [K in keyof C]: C[K] extends FormControl<infer U> ? U : never };
export declare class FormGroup<C extends Record<string, AbstractControl> = any> extends AbstractControl {
  constructor(controls: C);
  controls: C;
  getRawValue(): ValueOf<C>;
  markAllAsTouched(): void;
  patchValue(v: Partial<ValueOf<C>>): void;
}
export declare const Validators: { required: unknown; minLength(n: number): unknown; maxLength(n: number): unknown; pattern(p: string): unknown; min(n: number): unknown; max(n: number): unknown };
export declare const ReactiveFormsModule: any, FormsModule: any;
