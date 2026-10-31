# Сгенерировано specforge-analysis из requirements.json, FR-8
@FR-8 @must
Feature: FR-8 Передача событий в систему учёта ТМЦ
  Система должна передавать события выдачи, возврата и списания в корпоративную систему учёта ТМЦ

  @AC-FR-8-01 @positive
  Scenario: AC-FR-8-01 Передача события в Kafka
    Given Kafka доступна
    And Mock-потребитель подключен
    When Система публикует событие выдачи
    Then Событие доставлено в mock-потребитель
    And В таблице EventDeliveryLog создана запись со статусом delivered и timestamp

  @AC-FR-8-02 @negative
  Scenario: AC-FR-8-02 Kafka недоступна
    Given Kafka недоступна
    When Система пытается опубликовать событие
    Then Событие сохранено локально
    And Система повторяет публикацию после восстановления
