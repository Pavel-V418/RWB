# Сгенерировано specforge-analysis из requirements.json, FR-13
@FR-13 @must
Feature: FR-13 Бронирование сотрудником
  Система должна позволять сотруднику бронировать инструмент на дату

  @AC-FR-13-01 @positive
  Scenario: AC-FR-13-01 Бронирование сотрудником
    Given employee аутентифицирован
    When employee бронирует инструмент
    Then Бронирование создано
