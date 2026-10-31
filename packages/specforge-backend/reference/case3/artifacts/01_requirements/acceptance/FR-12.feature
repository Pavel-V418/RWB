# Сгенерировано specforge-analysis из requirements.json, FR-12
@FR-12 @must
Feature: FR-12 Добавление инструмента руководителем
  Система должна позволять руководителю склада добавлять инструмент в каталог

  @AC-FR-12-01 @positive
  Scenario: AC-FR-12-01 Добавление инструмента руководителем
    Given warehouse_manager аутентифицирован
    When warehouse_manager добавляет инструмент
    Then Инструмент сохранен в каталоге
