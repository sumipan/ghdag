# idempotency_key と correlation_id の責務分離

## 概要

`Task.idempotency_key` と `AuditContext.correlation_id` は別の目的を持つ 2 つのキーである。

- **`idempotency_key`**: 「同じタスクを 2 回登録しない」ためのキー。exec.jsonl の重複チェックに使われ、タスク完了後は意味を失う。
- **`correlation_id`**: 「入口（enqueue）と出口（task 完了）を串刺しする」ためのキー。audit.jsonl に永続記録され、タスク完了後もトレーシングに使える。

## 経路別の値の決まり方

| 経路 | `idempotency_key` | `correlation_id` |
|---|---|---|
| dispatcher（issuesmith） | `{workflow}:{handler}:{issue}` で自動生成（`dispatcher.py:130`） | `idempotency_key` と同値（`dispatcher.py:148`） |
| `ghdag run`（exec.md 直接実行） | exec.md の `idempotency_key` フィールドで手動指定（省略時 `None`） | `task.idempotency_key` を伝搬（`engine.py:321`）、未設定時は `None` |
| `append_exec_records()` 直接呼び出し | 呼び出し側が指定（省略時 `None`） | 呼び出し側が `AuditContext` を組み立て（未指定時 `None`） |

## 同値運用の正当性（dispatcher 経路）

dispatcher 経路では `idempotency_key = f"{workflow}:{handler}:{issue}"` が「同じハンドラを同じ Issue に対して 1 回だけ実行する」というスコープを定義し、`correlation_id` はそのハンドラ呼び出しの入口と出口を串刺しするスコープを定義する。これら 2 つのスコープは dispatcher 経由では自然に一致するため、同値代入が成立する。別キーに分けると dispatcher 側でキー発番ロジックが二重になり、不要な複雑性が生まれる。

## 利用ガイドライン

### dispatcher 経由（issuesmith）

`idempotency_key` と `correlation_id` はいずれも dispatcher が自動設定する。利用者が意識する必要はない。

### カスタム enqueue（`append_exec_records()` 直接呼び出し）

トレーシング目的には `correlation_id` を持つ `AuditContext` を生成して渡せば足りる。タスクの重複排除が必要な場合のみ `idempotency_key` を別途生成する。

```python
from ghdag.pipeline.audit import AuditContext
import uuid

audit_ctx = AuditContext(source="my_tool", correlation_id=str(uuid.uuid4()))
pipeline.submit(steps=steps, base_context=ctx, audit_context=audit_ctx)
```
