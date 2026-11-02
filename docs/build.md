# Сборка и зависимости

Команды ниже — PowerShell из корня checkout. Используйте одно Python-окружение; перед запуском очистите унаследованные `PYTHONHOME` и `PYTHONPATH`.

## Стандартная Windows x64

Нужны:

- Python **3.11–3.13 x64** (`>=3.11,<3.14`);
- Visual Studio **2022 Build Tools**, workload **Desktop development with C++**, MSVC x64 и Windows 10/11 SDK;
- Rust **stable**, target `x86_64-pc-windows-msvc`, установленный через rustup. Workspace использует **edition 2024**: нужен Rust не старее 1.85; отдельные locked dependencies могут требовать более свежий stable;
- Cargo в `PATH`; для linker/SDK используйте **Developer PowerShell for VS 2022 (x64)**.

```powershell
Remove-Item Env:PYTHONHOME -ErrorAction SilentlyContinue
Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
py -3.13 -m venv .venv
& ./.venv/Scripts/python.exe -m pip install -r requirements-dev.txt
rustup update stable
rustup default stable
rustc -Vv
cargo --version
& ./.venv/Scripts/python.exe build/build_worker.py
```

`requirements-dev.txt` закрепляет версии PySide6, NumPy, SciPy, Pillow, pytest, PyInstaller и scikit-image; допустимые runtime-диапазоны находятся в `pyproject.toml`. Первоначальная установка Python/Rust dependencies требует доступного индекса/сети. Это инструкция для стандартного окружения, **не свидетельство проверки на чистой Windows VM**.

## Native worker

Upstream VTracer закреплён на **`2500df76b5aea0ac22d15296b22722442f825c5b`**. Adapter: `native/worker/main.rs`; example `pbn_worker` в `vendor/vtracer/crates/vtracer/Cargo.toml` ссылается напрямую на него через `../../../../native/worker/main.rs`. Отдельной сборочной копии adapter нет. Pin не отменяет учёт локального diff и `Cargo.lock`.

Без `native/toolchain.json` builder использует установленный `cargo` из `PATH`. По умолчанию команда — `cargo build --locked --release -p vtracer --example pbn_worker`: Cargo может скачать недостающие зависимости, но не должен менять lock-файл. Для уже заполненного кеша:

```powershell
& ./.venv/Scripts/python.exe build/build_worker.py --offline
```

`--offline` добавляет Cargo `--offline`, не убирая `--locked`. Пустой/неполный кеш означает ошибку, а не автоматический выход в сеть.

### Необязательная локальная конфигурация

`native/toolchain.json` исключён из Git и **не нужен** для стандартной установки. При наличии builder читает объект `env` и накладывает его на окружение процесса. Можно задать `RUSTC`, `RUSTDOC`, `RUSTUP_HOME`, `CARGO_HOME`, `CARGO_TARGET_X86_64_PC_WINDOWS_MSVC_LINKER` и `LIB`. При заданном `RUSTC` используется соседний `cargo.exe`, сохраняя выбранный локальный toolchain. Неверный конфиг/путь не заменяется молча другим toolchain. Историческое поле `rustc` с текстом версии не считается доказательством текущего запуска.

`CARGO_HOME` из конфигурации или окружения сохраняется: существующий кеш можно переиспользовать. Без него Cargo выбирает стандартный пользовательский кеш. `PYTHONHOME` и `PYTHONPATH` удаляются перед запуском Cargo. `CARGO_TARGET_DIR` всегда перенаправляется в **этот checkout** (`native/target`), даже если локальный конфиг указывает на другой репозиторий: сборка не должна переписывать его target.

Конфигурацию с машинными путями, кеши и бинарники не добавляйте в Git и не загружайте как публичные исходники. `native/PROVENANCE.json` — локальный материал, не замена сохранённых исходников и pin.

При успехе появляются `assets/worker/pbn_worker.exe`, `assets/worker/build-receipt.json`, `reports/worker-build.json` и `reports/worker-build.log`. Receipt содержит фактическую команду, exit code, время и SHA256 скопированного бинарника, adapter и lock-файла. Сверяйте receipt с файлами конкретной сборки; старый EXE или receipt после неудачной попытки не доказывает её успех. Не копируйте случайный бинарник вместо исправления ошибки сборки.

## Windows portable

Сначала выполните native-сборку, tests и source smoke из [testing.md](testing.md). Затем:

```powershell
& ./.venv/Scripts/python.exe build/package_windows.py --output releases/public-check-1
```

Выходной каталог **не должен существовать**. Скрипт использует PyInstaller onedir, копирует лицензии, запускает frozen offscreen smoke, проверяет normal native export и ZIP, пишет `build-receipt.json`. Получаются portable-каталог с `_internal`, ZIP, логи и receipt. Не переименовывайте старый архив в новую версию: проверьте фактические release-имена и версию перед публикацией.

Скрипт упаковки не заменяет аудит соответствия commit: release-owner фиксирует commit SHA, тег, версии инструментов, native hash и SHA256 опубликованных assets по [release checklist](releasing.md).

## Чего сборка не доказывает

- Offscreen smoke — не визуальная приёмка, физическая печать или проверка чистой Windows.
- Внешние licensed artwork/fixtures не входят в checkout; ограничения tests описаны отдельно.
- Не заявляется побитовая воспроизводимость PyInstaller-архива: фиксируются конкретные входы и выходные хеши.
- Qt/PySide6, Rust dependencies и шрифты требуют notices и проверки права распространения. Не удаляйте существующие лицензии. Arial — системное предусловие, не включённый в пакет шрифт.

## Console encoding

On non-Russian Windows, use `$env:PYTHONUTF8 = "1"` before running Python commands. The application help currently contains Russian text; redirected cp1252 output cannot encode it. Public CI explicitly uses UTF-8.
