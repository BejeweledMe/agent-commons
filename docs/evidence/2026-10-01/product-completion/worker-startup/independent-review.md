# Независимое ревью implementation worker startup

Дата: 2026-10-01. Вердикт: **approved для frozen пятифайлового patch**. Существенных дефектов в проверенной границе не найдено. Это scoped correctness review, не реальный provider canary и не full release approval.

Объект: `/private/tmp/commons-worker-startup-20261001`, реальный diff/source/tests и `WORKER_STARTUP_INTEGRATION.json`. Patch SHA256: `9ff14b982d94fa6a146f625b71db9e5c4c02728f4a8f0c47965c5f8368a55c8a`. Все пять final hashes независимо проверены до и после tests. Main source/ledger, frozen source/tests, installed CLI/MCP, credentials и профили не изменялись. Provider/model не запускался. Synthetic тесты использовали собственный `/private/tmp`.

## Проверка по коду

- Startup добавляется только purpose `implementation` на builder profile. Существующий reviewer MCP-only/native-tool запрет не расширяется; verification instructions также не получают этот implementation startup автоматически.
- `DelegationStartupInput` содержит repo/state/child identity/CLI, не session nonce. `shlex.join` формирует каждый полный argv; paths с пробелами, одинарной кавычкой, `$()` и backticks передаются как литералы. Prefix явно задаёт `--repo`, `--state-root`, `--session-id`, `--read-only` до doctor/session/orient/inbox/task/claim reads. Поэтому ambient login-shell state/session exports не выбирают чужой workspace. Session show read-only не раскрывает nonce через текущий public view.
- `_startup_cli` повторно использует trusted executable resolver для configured MCP и только абсолютного `agent-commons` sibling. Resolver отказывает executable из delegated workspace, не-regular/non-executable, чужому владельцу и group/world-writable file. После symlink resolution parent bin должен совпадать, Python shebang — быть одинаковым; более ранний PATH CLI не выбирается. Это operator-trusted same-bin/shebang pairing, не криптографическая аттестация произвольного содержимого двух custom scripts.
- Missing/unsafe/unpaired CLI не заменяется случайным PATH бинарником и не устанавливается. Инструкция честно сообщает отсутствие trusted CLI, запрещает создание новой сессии и требует scoped needs-operator при невозможности обязательного startup. Canonical writes и terminal outcomes остаются scoped MCP.
- Static instruction содержит placeholder `session.` + 32 chars. Реальные session IDs создаются как `session.` + UUID hex (32 chars), поэтому размер/quoting placeholder соответствует нормальному identity. После **создания child session, но до reservation provider attempt** instruction пересобирается с реальным ID; библиотечный instruction сохраняется, verified skill bundle перекомпилируется. Placeholder не доходит до actual invocation.
- `replace(LaunchPlan, instruction=...)` повторно вызывает one-MiB проверку. `revalidate_bound_inputs` и `LaunchPlanner.build` сохраняют composed-size/provider checks, skill verification и exact invocation composition/fingerprint. Ошибки ConfigurationError/ValidationError на этом пути закрывают уже созданный child до provider attempt. Pre-child size refusal child не создаёт. Existing attempt/finalization cleanup не меняется этим patch.

## Независимые проверки

**95 focused tests passed за 39.64s**:

- `tests/runtime/test_delegation_instruction.py`;
- `tests/services/test_delegation_launch_plan.py`;
- `tests/runtime/test_launch_plan.py`;
- `tests/services/test_grok_launch_limits.py`;
- `tests/runtime/test_skill_projection.py`.

Среди них: hostile literal paths и foreign ambient state/session; шесть startup read commands реально исполнены через `/bin/sh` на synthetic CLI; до/после canonical/state bytes совпадают и command-substitution files не созданы. Actual broker child ID доходит до argv. Missing sibling, unsafe modes, другой interpreter и sibling symlink в другую installation отвергаются, даже если PATH содержит иной подходящий CLI. Context/skill drift, instruction bounds и failure cleanup regressions проходят.

Дополнительно:

1. `/private/tmp/commons-worker-startup-size-probe.py` искусственно увеличивает instruction выше one MiB отдельно **до** и **после** actual child binding. В обоих случаях refusal до reservation/runner: attempts=0, runner calls=0. До binding child отсутствует; после binding единственный созданный child имеет status closed. Использован fake runner, paid/provider execution отсутствует.
2. Exact baseline/current instruction comparison: **9 комбинаций** трёх reviewer profiles (Codex/Claude/Grok) и трёх purposes совпали byte-for-byte, даже при переданном startup. Reviewer инструкции не изменились.

Runner `/private/tmp/commons-worker-startup-review-run.py`; suite log `/private/tmp/commons-worker-startup-review-tests.log`. Интерпретатор main `.venv` read-only, frozen src через PYTHONPATH, Commons env удалены, bytecode/cacheprovider отключены, pytest basetemp изолирован.

## Ограничения

Реальная login-shell/provider failure была input контекстом root; независимая проверка воспроизвела её существенный механизм foreign ambient exports через synthetic `/bin/sh`, а не запуском авторизованного Astra. UI/assets/full merged make-check здесь не проверялись. После установки final wheel root должен повторить exact implementation profile/model canary: source и implementation instruction fingerprint изменились. Доверие к operator-owned installed entrypoints и отсутствие конкурентной злонамеренной подмены этих executable остаются существующей границей resolver.

## SHA256

| Файл | SHA256 |
|---|---|
| `WORKER_STARTUP.patch` | `9ff14b982d94fa6a146f625b71db9e5c4c02728f4a8f0c47965c5f8368a55c8a` |
| `WORKER_STARTUP_INTEGRATION.json` | `af69f49b0b9cd0e3ccc910dd7f535469691534909776d3c2d5736cc694fe92b0` |
| `src/agent_commons/services/delegation_instruction.py` | `60c11909e160a73f976f78b49c7f7d9de2a0e230cf7e4c7f2228cc4240ac0139` |
| `src/agent_commons/services/delegation_runtime.py` | `758e73d708732fa22615250ab280e1d825cd2c08bcd67745bb414e8a3ddf3426` |
| `tests/runtime/test_delegation_instruction.py` | `241c560824780fe57b7858ab318531d76a0579353ea0453e600423810a0fd4fb` |
| `tests/services/test_delegation_launch_plan.py` | `f06065b88f26f9251edd2f4c33d7a9b5b9e0f5f3ef6a273207f4b423ede910da` |
| `docs/PROTOCOL.md` | `85932063dcf862863e41f1ce37064d089515265338b1b82b8985dc445958e568` |
