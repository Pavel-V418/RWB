# Сгенерировано specforge-analysis из requirements.json, FR-11
@FR-11 @must
Feature: FR-11 Загрузка справочника сотрудников из файла
  Система должна загружать справочник сотрудников и номера пропусков из файла

  @AC-FR-11-01 @positive
  Scenario: AC-FR-11-01 Загрузка справочника
    Given system_admin аутентифицирован
    And Файл справочника валиден
    When system_admin загружает файл
    Then Данные импортированы в таблицу Employee
    And system_admin видит подтверждение

  @AC-FR-11-02 @negative
  Scenario: AC-FR-11-02 Невалидный файл
    Given system_admin аутентифицирован
    And Файл справочника невалиден
    When system_admin загружает файл
    Then Система показывает ошибку валидации
    And Данные не импортированы

  @AC-FR-11-03 @negative
  Scenario: AC-FR-11-03 Дубликаты в файле справочника
    Given system_admin аутентифицирован
    And Файл справочника содержит дубликаты номеров пропусков
    When system_admin загружает файл
    Then Система отклоняет импорт
    And Система показывает список дубликатов
