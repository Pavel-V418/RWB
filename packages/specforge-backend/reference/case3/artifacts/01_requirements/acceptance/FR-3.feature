# Сгенерировано specforge-analysis из requirements.json, FR-3
@FR-3 @must
Feature: FR-3 Выдача инструмента сотруднику
  Система должна позволять кладовщику выдать инструмент сотруднику, отсканировав QR-код инструмента и пропуск сотрудника

  @AC-FR-3-01 @positive
  Scenario: AC-FR-3-01 Выдача инструмента
    Given warehouse_keeper аутентифицирован
    And Инструмент доступен
    And Сотрудник найден по пропуску
    When warehouse_keeper сканирует QR-код инструмента и пропуск сотрудника
    Then Запись IssueRecord создана
    And Событие выдачи зафиксировано в MovementHistory

  @AC-FR-3-02 @negative
  Scenario: AC-FR-3-02 Выдача без связи
    Given warehouse_keeper аутентифицирован
    And Связь с сервером отсутствует
    When warehouse_keeper сканирует QR-код инструмента и пропуск сотрудника
    Then Операция сохранена локально в SyncQueue
    And warehouse_keeper видит подтверждение локальной выдачи

  @AC-FR-3-03 @negative
  Scenario: AC-FR-3-03 Выдача забронированного инструмента не тому сотруднику
    Given warehouse_keeper аутентифицирован
    And Инструмент забронирован сотрудником A
    And Сотрудник B найден по пропуску
    When warehouse_keeper сканирует QR-код инструмента и пропуск сотрудника B
    Then Система показывает отказ в выдаче
    And Запись IssueRecord не создана
