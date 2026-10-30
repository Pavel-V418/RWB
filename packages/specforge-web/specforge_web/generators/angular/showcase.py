"""Витрина UI Kit (/__kit): каждый компонент kit-manifest.json в минимальном использовании.

Назначение — шлюз самого манифеста: если `ng build` собирает эту страницу,
значит селекторы, модули и пути импорта из манифеста соответствуют установленной версии PrimeNG.
"""
from __future__ import annotations

# tag → (сниппет, теги, которые покрывает сниппет)
SNIPPETS: dict[str, str] = {
    "p-button": '<p-button label="Главное" icon="pi pi-check" data-testid="kit-button" /> <p-button label="Второстепенное" severity="secondary" data-testid="kit-button-2" /> <p-button label="Опасное" severity="danger" [outlined]="true" data-testid="kit-button-3" />',
    "p-table": '<p-table [value]="rows" dataKey="id" [rowHover]="true"><ng-template #header><tr><th pSortableColumn="name">Название <p-sortIcon field="name" /></th><th>Статус</th></tr></ng-template><ng-template #body let-row><tr><td>{{ row.name }}</td><td><p-tag [value]="row.status" severity="success" /></td></tr></ng-template></p-table>',
    "p-badge": '<p-badge value="3" severity="danger" />',
    "p-select": '<p-select [options]="options" optionLabel="label" optionValue="value" placeholder="Выберите" [(ngModel)]="selected" [showClear]="true" data-testid="kit-select" />',
    "p-multiselect": '<p-multiselect [options]="options" optionLabel="label" optionValue="value" placeholder="Несколько" [(ngModel)]="multi" data-testid="kit-multiselect" />',
    "p-datepicker": '<p-datepicker [(ngModel)]="date" dateFormat="dd.mm.yy" [showIcon]="true" data-testid="kit-datepicker" />',
    "p-inputnumber": '<p-inputnumber [(ngModel)]="num" [useGrouping]="false" data-testid="kit-inputnumber" />',
    "p-password": '<p-password [(ngModel)]="text" [feedback]="false" [toggleMask]="true" data-testid="kit-password" />',
    "p-toggleswitch": '<p-toggleswitch [(ngModel)]="flag" data-testid="kit-toggleswitch" />',
    "p-selectbutton": '<p-selectbutton [options]="options" optionLabel="label" optionValue="value" [(ngModel)]="selected" data-testid="kit-selectbutton" />',
    "p-iconfield": '<p-iconfield iconPosition="left"><p-inputicon styleClass="pi pi-search" /><input pInputText placeholder="Поиск" /></p-iconfield>',
    "p-card": '<p-card header="Карточка" subheader="Подзаголовок">Содержимое</p-card>',
    "p-panel": '<p-panel header="Панель" [toggleable]="true">Содержимое</p-panel>',
    "p-divider": "<p-divider />",
    "p-toolbar": '<p-toolbar><ng-template #start>Слева</ng-template><ng-template #end>Справа</ng-template></p-toolbar>',
    "p-breadcrumb": '<p-breadcrumb [model]="crumbs" [home]="home" />',
    "p-tabs": '<p-tabs value="0"><p-tablist><p-tab value="0">Первая</p-tab><p-tab value="1">Вторая</p-tab></p-tablist><p-tabpanels><p-tabpanel value="0">Один</p-tabpanel><p-tabpanel value="1">Два</p-tabpanel></p-tabpanels></p-tabs>',
    "p-menu": '<p-menu [model]="menu" />',
    "p-drawer": '<p-button label="Открыть панель" severity="secondary" (onClick)="drawer = true" data-testid="kit-drawer-open" /><p-drawer [(visible)]="drawer" header="Панель" position="right">Содержимое</p-drawer>',
    "p-dialog": '<p-button label="Открыть диалог" severity="secondary" (onClick)="dialog = true" data-testid="kit-dialog-open" /><p-dialog [(visible)]="dialog" header="Диалог" [modal]="true">Содержимое</p-dialog>',
    "p-confirmdialog": "<span class=\"sf-muted\">p-confirmdialog размещён в shell</span>",
    "p-toast": "<span class=\"sf-muted\">p-toast размещён в shell</span>",
    "p-message": '<p-message severity="error" text="Ошибка" /> <p-message severity="warn" text="Предупреждение" />',
    "p-skeleton": '<p-skeleton width="60%" /><p-skeleton height="2rem" />',
    "p-progressspinner": '<p-progressspinner strokeWidth="4" ariaLabel="Загрузка" />',
    "p-progressbar": '<p-progressbar [value]="40" />',
    "p-timeline": '<p-timeline [value]="events" align="left" styleClass="sf-timeline"><ng-template #content let-ev>{{ ev.title }}</ng-template></p-timeline>',
    "p-paginator": '<p-paginator [rows]="10" [totalRecords]="120" [first]="0" />',
    "p-avatar": '<p-avatar label="АБ" shape="circle" />',
    "p-chip": '<p-chip label="Фильтр" [removable]="true" />',
    "p-slider": '<p-slider [(ngModel)]="num" [min]="0" [max]="100" />',
    "p-popover": '<p-button label="Подробнее" severity="secondary" (onClick)="op.toggle($event)" data-testid="kit-popover-open" /><p-popover #op>Содержимое</p-popover>',
    "p-fileupload": '<p-fileupload mode="basic" chooseLabel="Файл" [auto]="false" [customUpload]="true" data-testid="kit-fileupload" />',
    "p-tag": '<p-tag value="Успех" severity="success" /> <p-tag value="Внимание" severity="warn" /> <p-tag value="Ошибка" severity="danger" />',
}
COVERED_BY = {"p-sortIcon": "p-table", "p-tablist": "p-tabs", "p-tab": "p-tabs", "p-tabpanels": "p-tabs", "p-tabpanel": "p-tabs",
              "p-inputicon": "p-iconfield"}

EXTRA = '<input pInputText placeholder="pInputText" /> <textarea pTextarea rows="2" placeholder="pTextarea"></textarea> <p-button icon="pi pi-info" [text]="true" pTooltip="Подсказка" ariaLabel="Подсказка" data-testid="kit-tooltip" />'


def showcase_component(manifest: dict) -> tuple[str, str]:
    comps = [c for c in manifest["components"] if not c.get("deprecatedInProject")]
    blocks, missing = [], []
    for c in comps:
        tag = c["tag"]
        if tag in COVERED_BY:
            continue
        snip = SNIPPETS.get(tag)
        if not snip:
            missing.append(tag)
            continue
        blocks.append(f'<p-card header="{tag}" class="sf-span-6" data-testid="kit-{tag}"><div class="sf-row">{snip}</div></p-card>')
    if missing:
        raise SystemExit(f"Нет сниппета витрины для: {', '.join(missing)} — добавьте в generators/angular/showcase.py")
    blocks.append(f'<p-card header="Директивы" class="sf-span-12"><div class="sf-row">{EXTRA}</div></p-card>')
    mods = sorted({(c["module"], c["from"]) for c in comps} | {(d["module"], d["from"]) for d in manifest["directives"]})
    imports = "\n".join(f"import {{ {m} }} from '{f}';" for m, f in mods)
    names = ", ".join(sorted({m for m, _ in mods}))
    ts = f"""// AUTO-GENERATED by SpecForge из kit-manifest.json — шлюз манифеста UI Kit.
import {{ ChangeDetectionStrategy, Component }} from '@angular/core';
import {{ FormsModule }} from '@angular/forms';
import {{ MenuItem }} from 'primeng/api';
{imports}

@Component({{
  selector: 'app-kit-showcase',
  imports: [FormsModule, {names}],
  templateUrl: './kit-showcase.component.html',
  changeDetection: ChangeDetectionStrategy.OnPush,
}})
export class KitShowcaseComponent {{
  protected rows = [{{ id: 1, name: 'Первая', status: 'Активна' }}, {{ id: 2, name: 'Вторая', status: 'Активна' }}];
  protected options = [{{ label: 'Один', value: 1 }}, {{ label: 'Два', value: 2 }}];
  protected selected: number | null = null;
  protected multi: number[] = [];
  protected date: Date | null = null;
  protected num = 10;
  protected text = '';
  protected flag = false;
  protected drawer = false;
  protected dialog = false;
  protected events = [{{ title: 'Событие 1' }}, {{ title: 'Событие 2' }}];
  protected crumbs: MenuItem[] = [{{ label: 'Раздел' }}, {{ label: 'Страница' }}];
  protected home: MenuItem = {{ icon: 'pi pi-home' }};
  protected menu: MenuItem[] = [{{ label: 'Пункт', icon: 'pi pi-circle' }}];
}}
"""
    html = '<div class="sf-page" data-testid="kit-showcase">\n<div class="sf-page-header"><h1>UI Kit: PrimeNG 20</h1></div>\n<div class="sf-grid">\n' + "\n".join(blocks) + "\n</div>\n</div>\n"
    return ts, html
