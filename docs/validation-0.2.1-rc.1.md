# Проверка 0.2.1-rc.1

Платформа: Windows, локальный прогон; это НЕ Gitea Actions.
Команда: `python -m pytest tests tests_engine -q -p no:cacheprovider --basetemp <new-directory>`.
Фактический результат: **260 passed, 1 skipped**, exit 0, 64.33s.

Исправления: единая версия PEP440/SemVer; исправлено ожидание изменённых пикселей fixture (25 по исходной карте); очистка собственного временного job при transient Windows lock имеет ограниченные повторные попытки и не маскирует исходное исключение. При постоянном отказе остаток явно отмечается в exception notes.

Skip: тест отсутствующего simplify API не применим, когда API уже реализован; не ошибка и не проверка качества иллюстрации.

Прогон использует существующие локальные fixtures и worker, поэтому чистый clone и Windows CI остаются отдельными задачами. Frozen smoke и проверка assets выполняются после merge/tag и фиксируются в release receipt.
