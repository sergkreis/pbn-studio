# Проверка изменений

## Запуск

PowerShell, корень репозитория; Python 3.11–3.13 и зависимости из `requirements-dev.txt`, worker собран по [build.md](build.md):

```powershell
Remove-Item Env:PYTHONHOME -ErrorAction SilentlyContinue
Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
$env:QT_QPA_PLATFORM = 'offscreen'
& ./.venv/Scripts/python.exe -m pytest tests tests_engine -ra
& ./.venv/Scripts/python.exe launch.py --smoke-test-dir reports/source-smoke-new
```

Smoke создаёт новую папку; используйте другое имя для повторного запуска. При ошибке сохраняйте `failure.txt`, exit code и очищенный лог. Source smoke с `frozen=false` не заменяет packaged smoke с `frozen=true`.

## Проверки public builder и frozen smoke

Малые tests команд и окружения не требуют Cargo, SDK, worker или внешних изображений:

```powershell
& ./.venv/Scripts/python.exe -m pytest tests/test_public_build.py -q
```

Проверяются locked-online default, явный `--offline`, отсутствие обязательного локального JSON, выбор настроенного toolchain, сохранение Cargo cache, очистка Python env и изоляция target внутри checkout. Это unit tests конфигурации, а не доказательство native-сборки.

Для frozen-проверки создайте новый пакет:

```powershell
& ./.venv/Scripts/python.exe build/package_windows.py --output releases/public-check-1
```

Packaging запускает frozen smoke сам. Проверяйте `smoke/receipt.json` (`frozen=true`, `status=passed`) и итоговый `build-receipt.json`; source smoke должен иметь `frozen=false`. Оба smoke используют программно созданный тестовый вход, а не подменяют внешнее licensed artwork. Успех на рабочей машине не означает проверку чистой Windows VM, физической печати или workflow CI.

## Предусловия и ограничения набора

- `pyproject.toml` задаёт `testpaths = ["tests"]`; поэтому `tests_engine` указан явно.
- Часть domain/UI-тестов использует `../research/stage1/native-gate/outputs`, включая `hole` и `checkerboard`.
- Внешнее licensed artwork Recraft и связанные acceptance-проекты **отсутствуют в публичном checkout**. Тесты с явным условием отсутствия raster/project дают skip; domain/UI-тесты без такого условия могут завершиться ошибкой отсутствующего fixture. Это ограничение полного набора, не результат успешной приёмки. Не копируйте и не включайте чужие изображения в Git только ради зелёного теста.
- `tests_engine/test_feature_preservation.py` также ожидает внешний `../images/02-fox-forest.png`; его отсутствие приводит к ошибке, не к skip. Не подставляйте другое изображение под именем acceptance fixture.
- Условия skip и offscreen-проверка не доказывают корректность непроверенного сценария.

## Что записывать в PR

| Поле | Содержание |
|---|---|
| Ревизия | полный проверенный commit SHA; dirty tree обозначать явно |
| Среда | Windows, Python, Qt, Rust/MSVC, worker SHA256 |
| Выполнение | точная команда, время запуска, exit code |
| Итог | фактические passed / failed / skipped и причины skip |
| Доказательства | очищенный лог/receipt как attachment или ссылка |
| Остаток | не проверено, заблокировано, ожидает ручной приёмки |

Для PR применяйте статусы **passed / failed / blocked / not run** только по фактам. Failed fixture остаётся failed, пока исправление не выполнено и не проверено. Одна успешная подвыборка не означает успех всего набора. После merge проверьте точный commit-кандидат; изменение версии или упаковки также требует проверок.

## Приёмка RC / stable

Проверить реальным запуском: старт приложения, открытие изображения, этапы и отмену, сохранение/повторное открытие проекта, правки и undo/redo, normal/diagnostic export, отказ при нерешённых замечаниях, размеры SVG/PDF/PNG, пакет и его отмену. Отдельно проверить тяжёлый вход, закрытие во время работы, чистую Windows x64, шрифты/runtime и физический отпечаток целевого формата. Отметить неподписанную сборку и ограничения распространения.

Публичный workflow `.github/workflows/windows.yml` запускает отдельную самодостаточную выборку тестов, сборку worker и source smoke. Фактический статус проверяйте в GitHub Actions. Это не полный legacy-набор: в исходном публичном checkout локальный полный запуск дал **193 passed, 139 failed, 4 skipped** из-за отсутствующих внешних fixtures; его перенос остаётся открытой задачей.
