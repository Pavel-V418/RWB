# Сгенерировано specforge-analysis из requirements.json, FR-6
@FR-6 @must
Feature: FR-6 Фиксация возврата инструмента
  Система должна фиксировать возврат инструмента с оценкой состояния: исправен, требует ремонта, утерян

  @AC-FR-6-01 @positive
  Scenario: AC-FR-6-01 Возврат инструмента
    Given Инструмент выдан сотруднику
    And warehouse_keeper аутентифицирован
    When warehouse_keeper сканирует QR-код и выбирает состояние «исправен»
    Then Запись ReturnRecord создана
    And Состояние инструмента обновлено на «исправен»

  @AC-FR-6-02 @negative
  Scenario: AC-FR-6-02 Возврат без активной выдачи
    Given Инструмент не выдан
    And warehouse_keeper аутентифицирован
    When warehouse_keeper пытается зафиксировать возврат
    Then Система показывает «Нет активной выдачи»
    And Запись ReturnRecord не создана

  @AC-FR-6-03 @positive
  Scenario: AC-FR-6-03 Возврат инструмента с оценкой «утерян»
    Given Инструмент выдан сотруднику
    And warehouse_keeper аутентифицирован
    When warehouse_keeper сканирует QR-код и выбирает состояние «утерян»
    Then Запись ReturnRecord создана
    And Инструмент помечен как списанный в каталоге
    And Событие списания опубликовано в Kafka
