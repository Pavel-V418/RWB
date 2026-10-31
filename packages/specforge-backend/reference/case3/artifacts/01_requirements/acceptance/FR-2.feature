# Сгенерировано specforge-analysis из requirements.json, FR-2
@FR-2 @must
Feature: FR-2 Печать QR-кода для инструмента
  Система должна печатать QR-код для каждой единицы инструмента

  @AC-FR-2-01 @positive
  Scenario: AC-FR-2-01 Генерация QR-кода
    Given Инструмент добавлен в каталог
    When Система генерирует QR-код
    Then QR-код отображается для печати
    And QR-код содержит инвентарный номер
