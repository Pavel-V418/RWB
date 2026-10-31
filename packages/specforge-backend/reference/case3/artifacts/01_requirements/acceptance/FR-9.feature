# Сгенерировано specforge-analysis из requirements.json, FR-9
@FR-9 @must
Feature: FR-9 История движения инструмента
  Система должна показывать историю движения каждой единицы инструмента

  @AC-FR-9-01 @positive
  Scenario: AC-FR-9-01 История движения
    Given Инструмент имеет историю движений
    And warehouse_manager аутентифицирован
    When warehouse_manager открывает историю движения
    Then История отображается в хронологическом порядке
